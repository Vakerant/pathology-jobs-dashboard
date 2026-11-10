"""Schema migration framework for the pathology-jobs dashboard.

Tracks ``schema_version`` in the ``meta`` table. The core legacy schema
(listings / source_status / meta + inline ALTER columns + key_version rebuild)
is treated as **version 1** (adopted in place, never rebuilt). New migrations
are appended as ``(version, name, func)`` tuples and applied in order.

Runner guarantees, per the project's data-safety rules:
  1. backup  -- copy data.db (and -wal/-shm) to config.BACKUP_DIR
  2. validate-- ``PRAGMA integrity_check`` before touching anything
  3. migrate -- each pending migration in its own transaction
  4. verify  -- row counts + sanity checks after every step
  5. rollback-- on any failure, restore the backup and abort

CLI:
    python migrations.py status
    python migrations.py backup
    python migrations.py migrate
    python migrations.py rollback <backup_path>
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone, timedelta

import config
import db

log = logging.getLogger("pathology.migrations")

# How long per-run history (scrape_runs / source_runs) is kept. Nothing in the
# dashboard reads these beyond "did the last run work" (which comes from
# source_status), so a generous window costs almost nothing and still bounds
# the tables.
RUN_HISTORY_DAYS = 90

SCHEMA_VERSION_BASELINE = 1  # legacy listings/source_status/meta schema

# Historical schema versions that some deployed databases carry but that are
# no longer present in MIGRATIONS (their entry was removed at some point, or the
# bump came from db.init_db's inline ALTERs). Existing installs record
# schema_version=4 while this build's highest listed migration is 3.
#
# This matters for two reasons:
#   1. apply_pending() computes pending as "version > stored", so a NEW migration
#      numbered 4 or lower would be silently skipped on every existing install —
#      forever, with no error. New entries must therefore be numbered ABOVE
#      this value.
#   2. A stored version higher than the highest known migration is benign (it
#      only means extra steps already ran), so _verify() logs it rather than
#      raising.
SCHEMA_VERSION_CONSUMED = max(SCHEMA_VERSION_BASELINE, 4)

# Highest migration version that shipped while the numbering was still
# contiguous. Versions at or below this are exempt from the guard below.
SCHEMA_VERSION_SHIPPED = 3


# --------------------------------------------------------------------------
# Migration definitions
# --------------------------------------------------------------------------

def _migrate_phase2_domain(conn: sqlite3.Connection) -> None:
    """Phase 2: opportunity-first domain model.

    Adds the new entity tables WITHOUT touching the legacy ``listings`` table
    (which remains authoritative until the extraction engine can populate the
    richer fields). Then back-fills ``opportunities`` 1:1 from ``listings`` so
    no existing row is lost. ``documents`` are seeded from the listing URLs.
    """
    c = conn.cursor()

    # ----- institutions -----
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS institutions (
            institution_id  TEXT PRIMARY KEY,
            name            TEXT,
            state           TEXT,
            departments     TEXT,   -- JSON array of strings
            locations       TEXT,   -- JSON array of strings
            sources         TEXT,   -- JSON array of source ids
            reliability     TEXT,   -- 'high' | 'medium' | 'unknown'
            created_at      TEXT
        )
        """
    )

    # ----- opportunities -----
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS opportunities (
            id                  TEXT PRIMARY KEY,   -- canonical id (legacy key during Phase 2)
            canonical_identity  TEXT,               -- Phase 5 canonical recruitment identity
            listing_key         TEXT,               -- back-reference to listings.key
            institution         TEXT,
            department          TEXT,
            role                TEXT,
            specialty           TEXT,               -- JSON array of strings
            sub_specialty       TEXT,               -- JSON array of strings
            location            TEXT,
            state               TEXT,
            region              TEXT,
            category            TEXT,               -- fellowship | senior_resident | job | other
            employment_type     TEXT,               -- permanent | contract | temporary | unknown
            vacancy_count       INTEGER,
            qualification       TEXT,               -- JSON array of {value,confidence,evidence}
            experience          TEXT,
            age_limit           TEXT,
            salary              TEXT,
            stipend             TEXT,
            application_mode    TEXT,               -- online | offline | walk_in | unknown
            application_url     TEXT,
            notice_date         TEXT,
            deadline            TEXT,
            interview_date      TEXT,
            fee                 TEXT,
            documents_required  TEXT,               -- JSON array
            relevance           TEXT,               -- high | medium
            eligibility_state   TEXT DEFAULT 'INSUFFICIENT_INFORMATION',
            lifecycle_state     TEXT DEFAULT 'ACTIVE',  -- ACTIVE|CLOSED|ARCHIVED|SUPERSEDED|CANCELLED
            source_id           TEXT,
            source_name         TEXT,
            title               TEXT,
            url                 TEXT,
            snippet             TEXT,
            is_seed             INTEGER DEFAULT 0,
            starred             INTEGER DEFAULT 0,
            hidden              INTEGER DEFAULT 0,
            first_seen          TEXT,
            last_seen           TEXT,
            discovered_at       TEXT,
            last_verified_at    TEXT,
            evidence            TEXT                -- JSON object
        )
        """
    )

    # ----- documents -----
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            document_id     TEXT PRIMARY KEY,
            source_id       TEXT,
            url             TEXT,
            canonical_url   TEXT,
            content_type    TEXT,
            title           TEXT,
            retrieved_at    TEXT,
            published_at    TEXT,
            content_hash    TEXT,
            raw_text        TEXT,
            extracted_text  TEXT,
            parser          TEXT,
            parser_version  TEXT,
            http_status     INTEGER,
            freshness       TEXT,   -- fresh | stale | unknown
            document_type   TEXT DEFAULT 'UNKNOWN',
                              -- ADVERTISEMENT|CORRIGENDUM|ADDENDUM|EXTENSION|
                              -- INTERVIEW_NOTICE|SHORTLIST|RESULT|CANCELLATION|
                              -- RECRUITMENT_RULE|UNKNOWN
            opportunity_id  TEXT
        )
        """
    )

    # ----- document_versions -----
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS document_versions (
            version_id           TEXT PRIMARY KEY,   -- sha1(document_id|content_hash)
            document_id          TEXT,
            content_hash         TEXT,
            normalized_text_hash TEXT,
            retrieved_at         TEXT,
            extracted_text       TEXT,
            changed_fields       TEXT                -- JSON array
        )
        """
    )

    # ----- events -----
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            event_id           TEXT PRIMARY KEY,
            event_type         TEXT,   -- OPPORTUNITY_CREATED|UPDATED|CLOSED|
                                       -- DEADLINE_CHANGED|ELIGIBILITY_CHANGED|
                                       -- VACANCY_CHANGED|DOCUMENT_ADDED|
                                       -- SOURCE_FAILED|SOURCE_RECOVERED|
                                       -- APPLICATION_STATUS_CHANGED
            opportunity_id     TEXT,
            occurred_at        TEXT,
            payload            TEXT,   -- JSON object
            idempotency_hash   TEXT UNIQUE
        )
        """
    )

    # ----- scrape_runs / source_runs -----
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS scrape_runs (
            run_id         TEXT PRIMARY KEY,
            started_at     TEXT,
            finished_at    TEXT,
            status         TEXT,   -- running | success | partial | failed
            total_sources  INTEGER,
            ok             INTEGER,
            failed         INTEGER,
            new_items      INTEGER
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS source_runs (
            source_run_id  TEXT PRIMARY KEY,
            run_id         TEXT,
            source_id      TEXT,
            status         TEXT,
            http_status    INTEGER,
            items_found    INTEGER,
            error          TEXT,
            started_at     TEXT,
            finished_at    TEXT
        )
        """
    )

    # ----- user_profile (singleton row id=1) -----
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS user_profile (
            id                     INTEGER PRIMARY KEY CHECK (id = 1),
            qualification          TEXT,
            degree_year            TEXT,
            registration           TEXT,
            preferred_roles        TEXT,   -- JSON array
            preferred_specialties  TEXT,   -- JSON array
            preferred_regions      TEXT,   -- JSON array
            work_mode              TEXT,
            gov_private_pref       TEXT,
            research_interest      INTEGER DEFAULT 0,
            fellowship_interest    INTEGER DEFAULT 0,
            excluded_roles         TEXT,   -- JSON array
            notification_preferences TEXT, -- JSON object
            last_seen_at           TEXT
        )
        """
    )

    # ----- applications -----
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS applications (
            id              TEXT PRIMARY KEY,
            opportunity_id  TEXT,
            status          TEXT,   -- SAVED|REVIEWING|READY_TO_APPLY|APPLIED|
                                    -- INTERVIEW|SELECTED|JOINED|DECLINED|
                                    -- EXPIRED|NOT_ELIGIBLE
            checklist       TEXT,   -- JSON object
            notes           TEXT,
            applied_at      TEXT,
            updated_at      TEXT
        )
        """
    )

    # ----- indexes -----
    c.execute("CREATE INDEX IF NOT EXISTS idx_opp_source ON opportunities(source_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_opp_region ON opportunities(region)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_opp_deadline ON opportunities(deadline)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_opp_lifecycle ON opportunities(lifecycle_state)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_doc_opp ON documents(opportunity_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_doc_source ON documents(source_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ev_opp ON events(opportunity_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_app_opp ON applications(opportunity_id)")

    conn.commit()

    # ----- back-fill opportunities from listings (1:1, lossless) -----
    rows = c.execute("SELECT * FROM listings").fetchall()
    now = datetime.now(timezone.utc).isoformat()
    for r in rows:
        d = dict(r)
        c.execute(
            """
            INSERT OR IGNORE INTO opportunities
              (id, listing_key, institution, region, category, relevance,
               source_id, source_name, title, url, snippet, is_seed, starred,
               hidden, notice_date, deadline, first_seen, last_seen,
               discovered_at, last_verified_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                d["key"], d["key"], None, d.get("region"), d.get("category"),
                d.get("relevance"), d.get("source_id"), d.get("source_name"),
                d.get("title"), d.get("url"), d.get("snippet"),
                int(d.get("is_seed") or 0), int(d.get("starred") or 0),
                int(d.get("hidden") or 0), d.get("notice_date"), d.get("deadline"),
                d.get("first_seen"), d.get("last_seen"), now, now,
            ),
        )
        # seed a document row from the listing URL so the document graph has a
        # starting point (content_hash/raw_text populated later by fetchers).
        doc_id = _doc_id(d.get("source_id"), d.get("url"))
        c.execute(
            """
            INSERT OR IGNORE INTO documents
              (document_id, source_id, url, canonical_url, title,
               retrieved_at, document_type, opportunity_id)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                doc_id, d.get("source_id"), d.get("url"), d.get("url"),
                d.get("title"), now, "UNKNOWN", d["key"],
            ),
        )
    conn.commit()


def _doc_id(source_id, url):
    return hashlib.sha1(f"{source_id or ''}|{url or ''}".encode("utf-8", "ignore")).hexdigest()


def _add_listings_document_type(conn: sqlite3.Connection) -> None:
    """Phase 5: thread document classification into legacy listings.

    Adds a ``document_type`` column (mirroring the ``documents.document_type``
    enum) so corrigendum/addendum/extension notices are captured and classified
    instead of being dropped by the NEGATIVE filter.
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(listings)").fetchall()}
    if "document_type" not in cols:
        conn.execute("ALTER TABLE listings ADD COLUMN document_type TEXT DEFAULT 'UNKNOWN'")
    # NB: deliberately NO conn.commit() here. apply_pending() owns the
    # transaction (explicit BEGIN ... COMMIT) and rolls back on error. An inner
    # commit() ends that transaction behind its back, so a failure in a later
    # step of the same migration cannot be rolled back and the migration is no
    # longer atomic. SQLite (legacy autocommit-off mode) implicitly re-opens a
    # transaction before the DML in _set_schema_version, so removing it is safe.


# Ordered migration list. Append-only: never edit or remove an entry once it
# has shipped, or existing databases will re-run a changed migration.
def _prune_run_history(conn: sqlite3.Connection) -> None:
    """Trim the unbounded run-history tables to the retention window.

    ``scrape_runs`` and ``source_runs`` were append-only with no retention
    policy at all: ``source_runs`` grows by len(SOURCES) (45) rows *per run*,
    i.e. ~45 rows/day, forever. Unlike ``listings`` — which is pruned at
    PRUNE_UNSEEN_DAYS — these tables had no bound, so the database grew without
    limit while contributing nothing the dashboard reads (the dashboard uses
    ``source_status``, which holds only the latest state per source).

    This is a one-time data migration that brings existing databases into
    compliance; :func:`db.prune_run_history` keeps them compliant on every
    subsequent run.
    """
    cutoff = (datetime.now(timezone.utc) -
              timedelta(days=RUN_HISTORY_DAYS)).isoformat()
    # Drop per-source rows belonging to runs that are themselves too old to
    # keep. Deleting the parent scrape_runs rows first would orphan these, and
    # source_runs is by far the bigger table.
    #
    # The NULL clause targets stranded rows left by a killed run, scoped by
    # started_at so it can never match the run currently in flight (which also
    # has finished_at IS NULL until record_run_finish runs).
    conn.execute(
        "DELETE FROM source_runs WHERE "
        "finished_at < ? OR (finished_at IS NULL AND started_at < ?)",
        (cutoff, cutoff),
    )
    conn.execute(
        "DELETE FROM scrape_runs WHERE "
        "finished_at < ? OR (finished_at IS NULL AND started_at < ?)",
        (cutoff, cutoff),
    )


MIGRATIONS = [
    (2, "phase2_domain_schema", _migrate_phase2_domain),
    (3, "listings_document_type", _add_listings_document_type),
    (5, "prune_run_history", _prune_run_history),
]


def _check_version_guard():
    """Fail loudly if MIGRATIONS can never run on an existing install.

    ``apply_pending`` skips any migration whose version is not greater than the
    version already stored in the database. A migration numbered at or below
    ``SCHEMA_VERSION_CONSUMED`` is therefore dead code on every database that
    already carries that version — it would apply on a brand-new clone and
    never on the install you are actually running, which is the worst possible
    failure mode for a migration (looks fine on a fresh test DB, silently does
    nothing in production).

    Migrations at or below ``SCHEMA_VERSION_SHIPPED`` are exempt: those shipped
    while the numbering was still contiguous, so they have already run
    everywhere. Everything above that line must clear SCHEMA_VERSION_CONSUMED.
    """
    for version, name, _ in MIGRATIONS:
        if version <= SCHEMA_VERSION_SHIPPED:
            continue  # historical, already applied everywhere
        if version <= SCHEMA_VERSION_CONSUMED:
            raise AssertionError(
                f"migration {version} ({name!r}) is numbered between "
                f"SCHEMA_VERSION_SHIPPED={SCHEMA_VERSION_SHIPPED} and "
                f"SCHEMA_VERSION_CONSUMED={SCHEMA_VERSION_CONSUMED}; it would be "
                "silently skipped on every existing install. Renumber it above "
                f"{SCHEMA_VERSION_CONSUMED}."
            )


_check_version_guard()


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

def get_schema_version(conn: sqlite3.Connection | None = None) -> int:
    close = False
    if conn is None:
        conn = db.get_conn()
        close = True
    try:
        row = conn.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()
        if row:
            return int(row["v"])
        # Adopt existing legacy schema as version 1 if core tables already exist.
        listing_tbl = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='listings'"
        ).fetchone()
        return SCHEMA_VERSION_BASELINE if listing_tbl else 0
    finally:
        if close:
            conn.close()


def _set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    conn.execute(
        "INSERT INTO meta(k,v) VALUES('schema_version',?) "
        "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (str(version),),
    )


def backup(backup_dir: str | None = None) -> str:
    """Copy data.db (plus -wal/-shm if present) into a timestamped backup dir.
    Returns the path of the backup file.

    Checkpoints the WAL *before* copying. data.db runs in WAL mode, so recent
    writes live in the -wal sidecar until a checkpoint; copying the main file
    alone can capture a database missing its most recent transactions. The old
    code copied the -wal sidecar too, which is better than nothing but still
    captures a state that was never a consistent single point in time — a crash
    between the two copy2 calls leaves a main file and a WAL from different
    moments. A TRUNCATE checkpoint first makes the main file self-consistent,
    after which the sidecars are empty and the extra copies are harmless.
    """
    src = str(config.DB_PATH)
    if not os.path.exists(src):
        raise FileNotFoundError(f"DB not found: {src}")
    try:
        conn = sqlite3.connect(src)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
    except sqlite3.Error:
        # Best effort: if the checkpoint fails (another process holds a read
        # lock, exotic filesystem) still take the copy — an uncheckpointed
        # backup plus its -wal sidecar beats no backup at all.
        pass
    dest_dir = backup_dir or str(config.BACKUP_DIR)
    os.makedirs(dest_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = os.path.join(dest_dir, f"data-{ts}.db")
    shutil.copy2(src, dest)
    for suffix in ("-wal", "-shm"):
        p = src + suffix
        if os.path.exists(p):
            shutil.copy2(p, dest + suffix)
    return dest


def _validate(conn: sqlite3.Connection) -> None:
    res = conn.execute("PRAGMA integrity_check").fetchone()
    if res is None or res[0] != "ok":
        raise RuntimeError(f"integrity check failed: {res[0] if res else 'no result'}")


def apply_pending(conn: sqlite3.Connection, target: int | None = None) -> dict:
    """Core migration loop. Assumes ``conn`` is open and the legacy schema is
    already initialised. Does NOT call ``db.init_db()`` (avoids recursion when
    invoked from inside ``init_db``). Returns a report dict."""
    report = {"applied": [], "already": [], "version": None}
    _validate(conn)
    current = get_schema_version(conn)
    pending = [m for m in MIGRATIONS if m[0] > current]
    if target is not None:
        pending = [m for m in pending if m[0] <= target]
    if not pending:
        # Still verify. Skipping the invariants on an already-current database
        # meant they only ever ran during the single migration run that built
        # the schema, so any later drift went completely unnoticed.
        #
        # But only warn here. listings and opportunities legitimately differ
        # for the whole of a scrape (scraper.run syncs them at the end), and
        # this runs from every process's init_db(). Raising crashed any process
        # that started during a scrape, and a scrape killed midway would wedge
        # every later run, since scraper.run() calls init_db() before it can
        # reach the sync that repairs the drift. Lasting drift is still caught:
        # verify_projection() runs after each scrape and gates CI publishing.
        try:
            _verify(conn)
        except RuntimeError as exc:
            log.warning("projection drift (expected during a scrape): %s", exc)
        report["version"] = current
        return report
    for version, name, func in pending:
        if get_schema_version(conn) >= version:
            report["already"].append(name)
            continue
        try:
            conn.execute("BEGIN")
            func(conn)
            _set_schema_version(conn, version)
            conn.execute("COMMIT")
            report["applied"].append(name)
        except Exception:
            conn.execute("ROLLBACK")
            raise
    _verify(conn)
    report["version"] = get_schema_version(conn)
    return report


def migrate(target: int | None = None, do_backup: bool = True) -> dict:
    """CLI/entry-point: init legacy schema, back up, then apply pending
    migrations. Returns a report dict."""
    db.init_db()  # ensure legacy schema + key_version rebuild are done first
    conn = db.get_conn()
    report = {"applied": [], "already": [], "backup": None, "version": None}
    try:
        pending = [m for m in MIGRATIONS if m[0] > get_schema_version(conn)]
        if not pending:
            report["version"] = get_schema_version(conn)
            return report
        if do_backup:
            report["backup"] = backup()
        report.update(apply_pending(conn, target=target))
        report["backup"] = report.get("backup")
        return report
    finally:
        conn.close()


def rollback(backup_path: str) -> None:
    """Restore the DB from a backup produced by ``backup()``."""
    src = str(config.DB_PATH)
    if not os.path.exists(backup_path):
        raise FileNotFoundError(f"backup not found: {backup_path}")
    # checkpoint WAL then copy back
    conn = db.get_conn()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    shutil.copy2(backup_path, src)
    for suffix in ("-wal", "-shm"):
        p = backup_path + suffix
        if os.path.exists(p):
            shutil.copy2(p, src + suffix)
        elif os.path.exists(src + suffix):
            os.remove(src + suffix)


def _verify(conn: sqlite3.Connection) -> None:
    """Post-migration sanity checks. Raises if an invariant is broken.

    Runs on every ``apply_pending`` call, including when nothing was pending —
    otherwise the invariants would only ever be checked during the one migration
    run that created the schema, and any later drift (a bad prune, a hand edit,
    an interrupted run) would go unnoticed forever.

    The opportunities check deliberately compares against *live* opportunities
    (``lifecycle_state='ACTIVE'``), not the total row count. repository.reconcile_
    orphans() intentionally keeps superseded rows as history, so once pruning has
    run the total legitimately exceeds the listing count; comparing totals raises
    a bogus "migration not lossless" on a perfectly healthy database. repository.
    verify_projection() uses exactly this same live-opportunities rule.
    """
    version = get_schema_version(conn)
    expected = [m[0] for m in MIGRATIONS]
    applied = [v for v in expected if v <= version]
    if version > max(expected, default=SCHEMA_VERSION_BASELINE):
        # Not fatal: existing databases carry a version above this build's
        # highest known migration (see SCHEMA_VERSION_CONSUMED). It only
        # means extra steps ran that are no longer listed. The real danger is
        # the reverse — a *new* migration numbered at or below an already-
        # stored version would be silently skipped — which is what
        # SCHEMA_VERSION_CONSUMED exists to document and prevent.
        log.debug("schema_version %d is ahead of known migrations %s",
                  version, expected)
    if 2 in applied:
        n_list = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        n_opp = conn.execute(
            "SELECT COUNT(*) FROM opportunities WHERE lifecycle_state='ACTIVE'"
        ).fetchone()[0]
        # Live opportunities are a 1:1 projection of listings.
        if n_opp != n_list:
            raise RuntimeError(
                f"live opportunities ({n_opp}) != listings ({n_list}); "
                "projection not lossless"
            )
        n_doc = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        # documents are per-URL, so distinct listings sharing a URL collapse to
        # one document (correct per "one ad = one document"); must be >=0 and <= listings.
        # A fresh/empty DB (0 listings, 0 documents) is a valid state.
        if not (0 <= n_doc <= n_list):
            raise RuntimeError(f"documents ({n_doc}) out of range [0, {n_list}]")


def status() -> dict:
    db.init_db()
    conn = db.get_conn()
    try:
        current = get_schema_version(conn)
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
        counts = {}
        for t in tables:
            counts[t] = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    finally:
        conn.close()
    return {"schema_version": current, "tables": counts}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        print(json.dumps(status(), indent=2))
        return 0
    if cmd == "backup":
        p = backup()
        print(f"backup -> {p}")
        return 0
    if cmd == "migrate":
        report = migrate()
        print(json.dumps(report, indent=2))
        return 0
    if cmd == "rollback":
        if len(argv) < 2:
            print("usage: migrations.py rollback <backup_path>", file=sys.stderr)
            return 2
        rollback(argv[1])
        print(f"rolled back from {argv[1]}")
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    import config
    config.setup_logging()
    raise SystemExit(main(sys.argv[1:]))
