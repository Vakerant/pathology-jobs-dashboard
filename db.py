"""SQLite storage layer for the pathology-jobs dashboard."""
import sqlite3
import re
import hashlib
import logging
import threading
from datetime import datetime, timezone, timedelta, date

import config

# Single source of truth for the DB path + prune/stale policy lives in config.py
# (env-overridable). ``config.DB_PATH`` already falls back to <project>/data.db.
DB_PATH = str(config.DB_PATH)

# Days a listing may stay unseen on its source page before we drop it.
PRUNE_UNSEEN_DAYS = config.PRUNE_UNSEEN_DAYS
# A notice whose only date is a publish/"uploaded on" date goes stale after this.
STALE_NOTICE_DAYS = config.STALE_NOTICE_DAYS
BACKUP_DIR = config.BACKUP_DIR

log = logging.getLogger("pathology.db")

# Run-history retention (scrape_runs / source_runs). These are append-only
# telemetry tables with no user-facing reader, so they just need a bound.
RUN_HISTORY_DAYS = 90
# Number of schema backups to retain in config.BACKUP_DIR.
BACKUP_KEEP = 10

# Resolved once per process by get_conn(): journal_mode is a persistent property
# of the database file, so after the first successful check it does not need to
# be re-issued on every connection open.
_WAL_CONFIRMED = threading.Event()


def get_conn():
    # timeout + WAL: the scraper writes from several threads concurrently
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    # busy_timeout is cheap and per-connection, so it is always set.
    conn.execute("PRAGMA busy_timeout=30000")
    # journal_mode=WAL is a *persistent database property*, not a per-connection
    # one: once set it is stored in the DB file and applies to every future
    # connection. Re-issuing it on every open was pure overhead — an extra
    # round-trip plus a write-lock acquisition, ~1400 times per scrape. Verified
    # once at import so a genuinely new/empty file still lands in WAL mode.
    if not _WAL_CONFIRMED.is_set():
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            if str(mode).lower() == "wal":
                _WAL_CONFIRMED.set()
            else:
                conn.execute("PRAGMA journal_mode=WAL")
                _WAL_CONFIRMED.set()
        except sqlite3.Error:
            # Fall back to setting it on every connection, as before.
            conn.execute("PRAGMA journal_mode=WAL")
    return conn


def upsert_listings(items):
    """Batch form of :func:`upsert_listing` — one connection, one transaction.

    A single scrape calls :func:`upsert_listing` once per listing (~1400 with
    the current source set, plus 21 more from seed.py). Each call used to open a
    connection, set pragmas, run a SELECT, run a write, commit, and close — so
    every scrape paid ~1400 connect/commit/close cycles and ~1400 separate WAL
    fsyncs from 10 worker threads contending on the same file.

    This shares a single connection and commits once at the end, which collapses
    the whole batch into one transaction. Returns the number of *new* keys, the
    same aggregate :func:`upsert_listing` callers care about.
    """
    items = list(items)
    if not items:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    new_count = 0
    conn = get_conn()
    try:
        c = conn.cursor()
        for item in items:
            key = make_key(item["source_id"], item["url"], item["title"])
            title = strip_serial(item["title"])
            row = c.execute("SELECT first_seen FROM listings WHERE key=?",
                            (key,)).fetchone()
            if row is None:
                new_count += 1
                c.execute(
                    """INSERT INTO listings
                       (key, source_id, source_name, region, category, title, url,
                        relevance, snippet, is_seed, notice_date, deadline,
                        document_type, first_seen, last_seen)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (key, item["source_id"], item["source_name"], item["region"],
                     item["category"], title, item["url"], item["relevance"],
                     item.get("snippet", ""), int(item.get("is_seed", 0)),
                     item.get("notice_date"), item.get("deadline"),
                     item.get("document_type", "UNKNOWN"), now, now),
                )
            else:
                # keep an existing notice_date/deadline if the new scrape
                # didn't find one
                c.execute(
                    """UPDATE listings SET last_seen=?, relevance=?, snippet=?,
                       source_name=?, region=?, category=?,
                       notice_date=COALESCE(?, notice_date),
                       deadline=COALESCE(?, deadline),
                       document_type=CASE WHEN ? = 'UNKNOWN' THEN document_type ELSE ? END
                       WHERE key=?""",
                    (now, item["relevance"], item.get("snippet", ""),
                     item["source_name"], item["region"], item["category"],
                     item.get("notice_date"), item.get("deadline"),
                     item.get("document_type", "UNKNOWN"),
                     item.get("document_type", "UNKNOWN"), key),
                )
        conn.commit()
    finally:
        conn.close()
    return new_count


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS listings (
            key         TEXT PRIMARY KEY,   -- hash of source_id+url+title
            source_id   TEXT,
            source_name TEXT,
            region      TEXT,
            category    TEXT,
            title       TEXT,
            url         TEXT,
            relevance   TEXT,               -- 'high' | 'medium'
            snippet     TEXT,
            is_seed     INTEGER DEFAULT 0,
            starred     INTEGER DEFAULT 0,
            hidden      INTEGER DEFAULT 0,
            first_seen  TEXT,
            last_seen   TEXT
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS source_status (
            source_id   TEXT PRIMARY KEY,
            source_name TEXT,
            region      TEXT,
            status      TEXT,               -- 'ok' | 'failed'
            http_status INTEGER,
            items_found INTEGER,
            error       TEXT,
            last_run    TEXT
        )
        """
    )
    c.execute(
        """CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)"""
    )
    # --- migrations ---
    cols = [r["name"] for r in c.execute("PRAGMA table_info(listings)").fetchall()]
    if "alerted" not in cols:
        c.execute("ALTER TABLE listings ADD COLUMN alerted INTEGER DEFAULT 0")
    if "notice_date" not in cols:
        c.execute("ALTER TABLE listings ADD COLUMN notice_date TEXT")  # ISO yyyy-mm-dd or NULL
    if "deadline" not in cols:
        c.execute("ALTER TABLE listings ADD COLUMN deadline TEXT")     # last-date/walk-in date or NULL
    conn.commit()
    conn.close()
    _migrate_keys_v2()
    _apply_schema_migrations()


def _apply_schema_migrations():
    """Apply any pending versioned schema migrations (see migrations.py).
    Imported lazily to avoid a circular import (migrations imports db).

    Backs up first when there is real work to do, so the auto-migration on
    boot never mutates the production DB without a recovery point."""
    import migrations

    conn = get_conn()
    try:
        pending = [m for m in migrations.MIGRATIONS if m[0] > migrations.get_schema_version(conn)]
        if pending:
            migrations.backup()
        migrations.apply_pending(conn)
    finally:
        conn.close()


def norm_title(title):
    """Canonical form of a title for dedup: strip the leading serial number some
    sites (e.g. AIIMS Bhubaneswar) prepend and renumber on every page change,
    normalise quotes, collapse whitespace, lowercase."""
    t = title or ""
    t = re.sub(r"^\s*\d{1,4}\s*[.):\-–]?\s+", "", t)   # "258 “NOTICE…" -> "“NOTICE…"
    t = t.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


def strip_serial(title):
    """Display version of the title without the leading serial number."""
    return re.sub(r"^\s*\d{1,4}\s*[.):\-–]?\s+", "", (title or "").strip()) or (title or "")


KEY_VERSION = "4"


def make_key(source_id, url, title):
    """Dedup key. A substantive title identifies a notice by itself — several
    sites (Metropolis, AIIMS BBSR) rotate tokens/serials in URL or title, so
    including the raw URL/title there creates endless duplicates. The URL only
    disambiguates genuinely short titles."""
    nt = norm_title(title)
    ident = nt if len(nt) >= 25 else f"{url}|{nt}"
    return hashlib.sha1(f"{source_id}|{ident}".encode("utf-8", "ignore")).hexdigest()


def _junk_title(nt):
    from sources import JUNK_TITLES
    return nt in JUNK_TITLES


def _migrate_keys_v2():
    """One-time rebuild of listing keys under the current KEY_VERSION scheme,
    merging the duplicates older key schemes created and dropping junk rows."""
    if get_meta("key_version") == KEY_VERSION:
        return
    conn = get_conn()
    c = conn.cursor()
    rows = [dict(r) for r in c.execute("SELECT * FROM listings").fetchall()]
    merged = {}
    for r in rows:
        if not r["is_seed"] and _junk_title(norm_title(r["title"])):
            continue  # bare "View Details"-type rows: drop
        nk = make_key(r["source_id"], r["url"], r["title"])
        m = merged.get(nk)
        if m is None:
            r["title"] = strip_serial(r["title"])
            merged[nk] = r
        else:
            m["first_seen"] = min(m["first_seen"], r["first_seen"])
            if r["last_seen"] > m["last_seen"]:
                m["last_seen"] = r["last_seen"]
                m["title"] = strip_serial(r["title"])
            for f in ("starred", "hidden", "alerted", "is_seed"):
                m[f] = max(m[f] or 0, r[f] or 0)
            m["notice_date"] = max(filter(None, [m.get("notice_date"), r.get("notice_date")]), default=None)
            m["deadline"] = max(filter(None, [m.get("deadline"), r.get("deadline")]), default=None)
    c.execute("DELETE FROM listings")
    for nk, r in merged.items():
        c.execute(
            """INSERT INTO listings (key, source_id, source_name, region, category, title,
               url, relevance, snippet, is_seed, starred, hidden, alerted,
               notice_date, deadline, first_seen, last_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (nk, r["source_id"], r["source_name"], r["region"], r["category"], r["title"],
             r["url"], r["relevance"], r["snippet"], r["is_seed"] or 0, r["starred"] or 0,
             r["hidden"] or 0, r["alerted"] or 0, r.get("notice_date"), r.get("deadline"),
             r["first_seen"], r["last_seen"]),
        )
    conn.commit()
    conn.close()
    set_meta("key_version", KEY_VERSION)


def upsert_listing(item):
    """item: dict with source_id, source_name, region, category, title, url,
    relevance, snippet, is_seed. Preserves starred/hidden flags + first_seen."""
    now = datetime.now(timezone.utc).isoformat()
    key = make_key(item["source_id"], item["url"], item["title"])
    title = strip_serial(item["title"])
    conn = get_conn()
    c = conn.cursor()
    row = c.execute("SELECT first_seen FROM listings WHERE key=?", (key,)).fetchone()
    is_new = row is None
    if is_new:
        c.execute(
            """INSERT INTO listings
               (key, source_id, source_name, region, category, title, url,
                relevance, snippet, is_seed, notice_date, deadline,
                document_type, first_seen, last_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (key, item["source_id"], item["source_name"], item["region"],
             item["category"], title, item["url"], item["relevance"],
             item.get("snippet", ""), int(item.get("is_seed", 0)),
             item.get("notice_date"), item.get("deadline"),
             item.get("document_type", "UNKNOWN"), now, now),
        )
    else:
        # keep an existing notice_date/deadline if the new scrape didn't find one
        c.execute(
            """UPDATE listings SET last_seen=?, relevance=?, snippet=?,
               source_name=?, region=?, category=?,
               notice_date=COALESCE(?, notice_date),
               deadline=COALESCE(?, deadline),
               document_type=CASE WHEN ? = 'UNKNOWN' THEN document_type ELSE ? END
               WHERE key=?""",
            (now, item["relevance"], item.get("snippet", ""), item["source_name"],
             item["region"], item["category"], item.get("notice_date"),
             item.get("deadline"), item.get("document_type", "UNKNOWN"),
             item.get("document_type", "UNKNOWN"), key),
        )
    conn.commit()
    conn.close()
    return is_new


def prune_stale(days=PRUNE_UNSEEN_DAYS):
    """Drop scraped listings that vanished from their source page `days` ago.
    Seeds and starred items are never pruned."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = get_conn()
    n = conn.execute(
        "DELETE FROM listings WHERE is_seed=0 AND starred=0 AND last_seen < ?",
        (cutoff,),
    ).rowcount
    conn.commit()
    conn.close()
    return n


def prune_run_history(days=None):
    """Keep ``scrape_runs`` / ``source_runs`` bounded. Safe to call every run.

    These tables are append-only telemetry: ``source_runs`` gains one row per
    source per run (~45 rows/day at the current source count) and nothing ever
    removed them, so the database grew without limit. Unlike ``listings`` there
    is no user-facing value in keeping years of run history — the dashboard
    reads per-source *current* state from ``source_status``, not this table.

    Failures are swallowed: retention is housekeeping, and a cleanup error must
    never take down a scrape that otherwise succeeded.
    """
    if days is None:
        days = RUN_HISTORY_DAYS
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = None
    try:
        conn = get_conn()
        # source_runs is the big one; drop its old rows first so the parent
        # scrape_runs rows we delete below are not left pointing at nothing.
        #
        # The NULL clause targets *stranded* rows left by a killed run — but it
        # must be scoped by started_at, because the run currently in flight
        # also has finished_at IS NULL until record_run_finish() runs. An
        # unscoped `finished_at IS NULL` deletes the live run row.
        src = conn.execute(
            "DELETE FROM source_runs WHERE "
            "finished_at < ? OR (finished_at IS NULL AND started_at < ?)",
            (cutoff, cutoff),
        ).rowcount
        runs = conn.execute(
            "DELETE FROM scrape_runs WHERE "
            "finished_at < ? OR (finished_at IS NULL AND started_at < ?)",
            (cutoff, cutoff),
        ).rowcount
        conn.commit()
        return src, runs
    except Exception:  # noqa: BLE001 - housekeeping must never fail a scrape
        log.warning("prune_run_history failed", exc_info=True)
        return 0, 0
    finally:
        if conn is not None:
            conn.close()


def prune_backups(keep=BACKUP_KEEP):
    """Keep only the newest `keep` backup files. Never raises."""
    import os
    import glob
    try:
        files = sorted(glob.glob(os.path.join(str(BACKUP_DIR), "data-*.db")))
        for stale in files[:-keep] if keep > 0 else files:
            for path in (stale, stale + "-wal", stale + "-shm"):
                try:
                    os.remove(path)
                except OSError:
                    pass
    except Exception:  # noqa: BLE001 - housekeeping must never fail a scrape
        log.warning("prune_backups failed", exc_info=True)


def enrich(listings):
    """Add live `expired` / `is_new` / `closing_soon` flags to listing dicts.
    Shared by the Flask API and the static export so both stay consistent.

    - a real deadline (last date / walk-in date) in the past => expired
    - only a publish date => stale (expired) after STALE_NOTICE_DAYS
    - seeds never expire (they're ongoing/recurring opportunities)
    """
    today = date.today()
    today_iso = today.isoformat()
    stale_cut = (today - timedelta(days=STALE_NOTICE_DAYS)).isoformat()
    new_cut = (today - timedelta(days=21)).isoformat()
    seen_cut = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    soon_cut = (today + timedelta(days=7)).isoformat()
    for l in listings:
        dl, nd = l.get("deadline"), l.get("notice_date")
        if l.get("is_seed"):
            l["expired"] = False
        elif dl:
            l["expired"] = dl < today_iso
        else:
            l["expired"] = bool(nd and nd < stale_cut)
        l["closing_soon"] = bool(dl and today_iso <= dl <= soon_cut)
        if nd or dl:
            l["is_new"] = (nd or dl) >= new_cut
        else:
            l["is_new"] = bool(l["first_seen"] >= seen_cut and not l["is_seed"])
    return listings


def record_status(source_id, source_name, region, status, http_status, items_found, error=""):
    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    conn.execute(
        """INSERT INTO source_status
           (source_id, source_name, region, status, http_status, items_found, error, last_run)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(source_id) DO UPDATE SET
             source_name=excluded.source_name, region=excluded.region,
             status=excluded.status, http_status=excluded.http_status,
             items_found=excluded.items_found, error=excluded.error,
             last_run=excluded.last_run""",
        (source_id, source_name, region, status, http_status, items_found, error, now),
    )
    conn.commit()
    conn.close()


def record_run_start(run_id, started_at, total_sources):
    """Open a `scrape_runs` row. Written BEFORE any network work so a run that
    is killed or crashes still leaves a 'running' row behind.

    Writing that trace is only useful if 'running' reliably means "in flight
    right now". A run that is SIGTERM'd (systemd timeout, manual stop) or that
    dies with its parent never reaches record_run_finish, so its row would sit
    at 'running' forever and become indistinguishable from a live run. Since
    only one scrape can meaningfully be in flight at a time, any pre-existing
    'running' row at the moment a new run starts is by definition dead, and is
    closed out as 'interrupted' rather than left to rot.
    """
    conn = get_conn()
    conn.execute(
        "UPDATE scrape_runs SET status='interrupted', finished_at=?"
        " WHERE status='running' AND run_id<>?",
        (started_at, run_id),
    )
    conn.execute(
        "INSERT OR REPLACE INTO scrape_runs (run_id, started_at, status, total_sources)"
        " VALUES (?,?,'running',?)",
        (run_id, started_at, total_sources),
    )
    conn.commit()
    conn.close()


def record_run_finish(run_id, finished_at, status, total_sources, ok, failed, new_items):
    conn = get_conn()
    conn.execute(
        """UPDATE scrape_runs
              SET finished_at=?, status=?, total_sources=?, ok=?, failed=?, new_items=?
            WHERE run_id=?""",
        (finished_at, status, total_sources, ok, failed, new_items, run_id),
    )
    conn.commit()
    conn.close()


def record_source_run(run_id, source_id, status, http_status, items_found, error=""):
    conn = get_conn()
    conn.execute(
        """INSERT OR REPLACE INTO source_runs
           (source_run_id, run_id, source_id, status, http_status, items_found, error, started_at, finished_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (f"{run_id}:{source_id}", run_id, source_id, status,
         http_status, items_found, error,
         datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def get_source_http_status(source_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT http_status FROM source_status WHERE source_id=?", (source_id,)
    ).fetchone()
    conn.close()
    return (row or [0])[0] or 0


def get_source_items_found(source_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT items_found FROM source_status WHERE source_id=?", (source_id,)
    ).fetchone()
    conn.close()
    return (row or [0])[0] or 0


def set_meta(k, v):
    conn = get_conn()
    conn.execute("INSERT INTO meta(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))
    conn.commit()
    conn.close()


def get_meta(k, default=None):
    conn = get_conn()
    row = conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    conn.close()
    return row["v"] if row else default


# Allowed flag fields — keep in sync with app.ALLOWED_FLAG_FIELDS
_ALLOWED_FLAGS = {"starred", "hidden"}

def set_flag(key, field, value):
    """Update a flag column safely. Returns number of rows updated (0 if key not found).

    Raises ValueError if field is not in allowlist — never interpolate unchecked
    input into SQL (assert is stripped with -O).
    """
    if field not in _ALLOWED_FLAGS:
        raise ValueError(f"field must be one of {sorted(_ALLOWED_FLAGS)}")
    if not isinstance(value, int) or value not in (0, 1):
        raise ValueError("value must be 0 or 1")
    conn = get_conn()
    # field is allowlisted above, so interpolation is safe; value/key are bound params
    cur = conn.execute(f"UPDATE listings SET {field}=? WHERE key=?", (int(value), key))
    n = cur.rowcount
    conn.commit()
    conn.close()
    return n


def fetch_listings():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM listings ORDER BY first_seen DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def fetch_status():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM source_status ORDER BY region, source_name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# Every aggregate that feeds the /api/data payload, collapsed into one cheap
# fingerprint. See data_fingerprint() for why this exists.
_FINGERPRINT_SQL = """
SELECT (SELECT COUNT(*) || '|' || COALESCE(MAX(last_seen), '') || '|'
               || COALESCE(SUM(starred), 0) || '|' || COALESCE(SUM(hidden), 0) || '|'
               || COALESCE(SUM(alerted), 0) || '|' || COALESCE(SUM(is_seed), 0) || '|'
               || COALESCE(SUM(relevance = 'high'), 0) || '|'
               || COALESCE(SUM(LENGTH(COALESCE(title, ''))), 0) || '|'
               || COALESCE(SUM(LENGTH(COALESCE(snippet, ''))), 0) || '|'
               || COALESCE(SUM(LENGTH(COALESCE(url, ''))), 0) || '|'
               || COALESCE(SUM(LENGTH(COALESCE(source_id, ''))), 0) || '|'
               || COALESCE(SUM(LENGTH(COALESCE(notice_date, ''))), 0) || '|'
               || COALESCE(SUM(LENGTH(COALESCE(deadline, ''))), 0) || '|'
               || COALESCE(SUM(LENGTH(COALESCE(document_type, ''))), 0)
        FROM listings),
       (SELECT COUNT(*) || '|' || COALESCE(SUM(status = 'ok'), 0) || '|'
               || COALESCE(SUM(status = 'failed'), 0) || '|'
               || COALESCE(SUM(COALESCE(items_found, 0)), 0) || '|'
               || COALESCE(SUM(LENGTH(COALESCE(error, ''))), 0) || '|'
               || COALESCE(MAX(last_run), '')
        FROM source_status)
"""


def data_fingerprint(extra=""):
    """Cheap fingerprint of everything that feeds the `/api/data` payload.

    `/api/data` serialises ~1.4 MB of listings and the dashboard re-fetches it
    every 60s. Hashing the serialised body would mean paying that cost *just to
    find out nothing changed* (~34 ms of json.dumps). Instead this returns a
    single scalar built from SQL aggregates (~4 ms), so an unchanged dashboard
    can be answered with a 304 and no body at all.

    Coverage -- every column any write path in this codebase can touch:

      * `listings`     -- COUNT/MAX/SUMs plus LENGTH() of every free-text
                          column, so an in-place edit to a title, snippet, URL,
                          date or document_type still moves the fingerprint.
                          `upsert_listing` also always bumps `last_seen`, so
                          any scraped change is caught there too.
      * `source_status`-- drives sources_ok / sources_failed in the stats block,
                          and is updated by every scrape even when no new
                          listing is found.
      * `meta`         -- the caller folds in the specific keys it renders
                          (see app.api_data) by passing `extra`.

    What this deliberately does NOT capture: derived booleans that depend on
    the clock. `enrich()` computes expired / closing_soon / is_new from
    *today's* date, so those can change at midnight with no DB write at all.
    The caller therefore mixes `date.today().isoformat()` into the hashed value
    too, which invalidates the cache at the day boundary.

    Collision risk is a length-preserving edit that also leaves every SUM
    untouched (e.g. swapping two words of the same length in one title on the
    same day without touching last_seen). No write path in this repo does that,
    and the worst case is a stale card for one poll cycle, not data loss.
    """
    conn = get_conn()
    try:
        parts = conn.execute(_FINGERPRINT_SQL).fetchone()
    except sqlite3.Error:
        log.warning("data_fingerprint: aggregate query failed; ignoring cache", exc_info=True)
        return None
    finally:
        conn.close()
    return "|".join((parts[0] or "", parts[1] or "", extra or ""))


# ---------------- alerting ----------------

def fetch_unalerted_pathology():
    """New, visible, HIGH-relevance (pathology) listings not yet emailed.

    Excludes notices that are already stale by *either* clock:

    - ``notice_date`` in the past — the posting itself is old, so the user has
      already seen it and re-mailing it is pure noise.
    - ``deadline`` in the past — the notice was published recently, so it passes
      the notice_date test, but its application window has already closed.
      These are the most annoying false "new post" emails there are: the alert
      arrives for something nobody can act on. ``deadline`` is stored as an ISO
      date string, which sorts correctly as text, so a plain ``>=`` comparison
      against today's ISO date is sound.
    """
    from datetime import date
    today = date.today().isoformat()
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM listings
           WHERE relevance='high' AND alerted=0 AND hidden=0
             AND (notice_date IS NULL OR notice_date >= ?)
             AND (deadline IS NULL OR deadline >= ?)
           ORDER BY region, first_seen DESC""",
        (today, today),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_alerted(keys):
    if not keys:
        return
    conn = get_conn()
    conn.executemany("UPDATE listings SET alerted=1 WHERE key=?", [(k,) for k in keys])
    conn.commit()
    conn.close()


def baseline_mark_all_alerted():
    """Mark every existing listing as already-alerted (run once at setup so the
    first real email only contains genuinely new posts, not the whole backfill)."""
    conn = get_conn()
    n = conn.execute("UPDATE listings SET alerted=1").rowcount
    conn.commit()
    conn.close()
    return n
