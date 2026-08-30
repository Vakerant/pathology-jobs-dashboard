"""SQLite storage layer for the pathology-jobs dashboard."""
import sqlite3
import os
import re
import hashlib
from datetime import datetime, timezone, timedelta, date

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.db")

# Days a listing may stay unseen on its source page before we drop it.
PRUNE_UNSEEN_DAYS = 45
# A notice whose only date is a publish/"uploaded on" date goes stale after this.
STALE_NOTICE_DAYS = 45


def get_conn():
    # timeout + WAL: the scraper writes from several threads concurrently
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


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
                relevance, snippet, is_seed, notice_date, deadline, first_seen, last_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (key, item["source_id"], item["source_name"], item["region"],
             item["category"], title, item["url"], item["relevance"],
             item.get("snippet", ""), int(item.get("is_seed", 0)),
             item.get("notice_date"), item.get("deadline"), now, now),
        )
    else:
        # keep an existing notice_date/deadline if the new scrape didn't find one
        c.execute(
            """UPDATE listings SET last_seen=?, relevance=?, snippet=?,
               source_name=?, region=?, category=?,
               notice_date=COALESCE(?, notice_date),
               deadline=COALESCE(?, deadline) WHERE key=?""",
            (now, item["relevance"], item.get("snippet", ""), item["source_name"],
             item["region"], item["category"], item.get("notice_date"),
             item.get("deadline"), key),
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


# ---------------- alerting ----------------

def fetch_unalerted_pathology():
    """New, visible, HIGH-relevance (pathology) listings not yet emailed,
    excluding ones whose notice date is already in the past."""
    from datetime import date
    today = date.today().isoformat()
    conn = get_conn()
    rows = conn.execute(
        """SELECT * FROM listings
           WHERE relevance='high' AND alerted=0 AND hidden=0
             AND (notice_date IS NULL OR notice_date >= ?)
           ORDER BY region, first_seen DESC""",
        (today,),
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
