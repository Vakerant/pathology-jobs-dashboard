"""Purge listings (and their projection rows) for sources removed from sources.py.

Why this exists
---------------
Dropping a source from ``sources.SOURCES`` stops *new* scrapes for it, but it
does NOT remove the rows already stored. Those rows then linger until
``db.prune_stale`` ages them out (``PRUNE_UNSEEN_DAYS``), during which they keep
showing on the dashboard. When a source is removed on purpose -- e.g. the
private diagnostic chains the user asked to drop -- that lingering window is
exactly the wrong behaviour.

This script deletes, in dependency order:
    documents -> opportunities -> listings
for every listing whose ``source_id`` is in ``REMOVED_SOURCE_IDS``.

It is idempotent: running it twice is a no-op the second time. The source
removal itself stays in git history (``sources.py``), so this is recoverable by
re-adding the source and re-scraping.

Usage
-----
    python purge_removed_sources.py            # dry run, prints what it would do
    python purge_removed_sources.py --apply    # actually delete
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

import db

# Source ids that were deliberately removed from sources.py. Keep in sync with
# git history: `git log -p -- sources.py` shows which ids were dropped and when.
REMOVED_SOURCE_IDS = [
    "lalpathlabs",   # Dr Lal PathLabs
    "metropolis",    # Metropolis Healthcare
    "agilus",        # Agilus Diagnostics (formerly SRL)
]

# Historic source_name spellings, because listings were keyed off the name at
# scrape time and a few rows carry a "- Careers" variant. Matching by name too
# means a source removed before it had a stable id is still cleaned up.
REMOVED_SOURCE_NAMES = [
    "Dr Lal PathLabs",
    "Dr Lal PathLabs – Careers",
    "Metropolis Healthcare",
    "Metropolis Healthcare – Careers",
    "Agilus Diagnostics (SRL)",
]

# Regions that only ever belonged to the removed private chains.
REMOVED_REGIONS = ["Private / Metro"]


def _off_scope_keys(conn: sqlite3.Connection) -> list[str]:
    """Keys of stored listings that match the strict-scope blocklist.

    Scoped deliberately NARROW: only rows whose title contains an explicit
    OFF_SCOPE_PATHS term (oral pathology / dental / veterinary / ...). This is
    *not* "everything scraper.relevance() rejects" -- relevance() also returns
    None for legitimate listings whose title happens to omit a pathology
    keyword, so using it as the delete predicate would destroy good data.

    These rows were scraped before OFF_SCOPE_PATHS existed. The filter itself
    works; this only cleans up the backlog it left behind.
    """
    from sources import OFF_SCOPE_PATHS

    keys, titles = [], []
    for key, title in conn.execute("SELECT key, title FROM listings"):
        low = (title or "").lower()
        if any(k in low for k in OFF_SCOPE_PATHS):
            keys.append(key)
            titles.append(title)

    if titles:
        print(f"off-scope backlog (title matches OFF_SCOPE_PATHS): {len(keys)}")
        for t in titles[:20]:
            print(f"   - {t[:88]}")
    return keys


def _predicates(conn: sqlite3.Connection) -> tuple[str, list]:
    """Build the shared WHERE clause for source_id / source_name / region."""
    clauses = []
    params: list = []
    for column, values in (
        ("source_id", REMOVED_SOURCE_IDS),
        ("source_name", REMOVED_SOURCE_NAMES),
        ("region", REMOVED_REGIONS),
    ):
        if values:
            clauses.append(f"{column} IN ({','.join('?' * len(values))})")
            params.extend(values)
    if not clauses:
        raise SystemExit("nothing configured to purge -- refusing to run")
    return " OR ".join(clauses), params


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the deletes (default is a dry run)",
    )
    args = parser.parse_args()

    db.init_db()
    conn = sqlite3.connect(db.DB_PATH)
    where, params = _predicates(conn)

    listing_keys = [
        r[0] for r in conn.execute(f"SELECT key FROM listings WHERE {where}", params)
    ]
    # Backlog rows the strict-scope filter would now reject. Deduplicated
    # against the source-removal set so a row matching both is deleted once.
    listing_keys = sorted(set(listing_keys) | set(_off_scope_keys(conn)))
    opportunity_ids = [
        r[0]
        for r in conn.execute(
            f"SELECT id FROM opportunities WHERE {where}", params
        )
    ]
    # Opportunities mirror listings by key, so catch any mirror row whose
    # listing_key is going away even if its own source columns drifted.
    if listing_keys:
        qs = ",".join("?" * len(listing_keys))
        opportunity_ids += [
            r[0]
            for r in conn.execute(
                f"SELECT id FROM opportunities WHERE listing_key IN ({qs})",
                listing_keys,
            )
        ]
    opportunity_ids = sorted(set(opportunity_ids))

    doc_ids: list[str] = []
    if opportunity_ids:
        qs = ",".join("?" * len(opportunity_ids))
        doc_ids = [
            r[0]
            for r in conn.execute(
                f"SELECT document_id FROM documents WHERE opportunity_id IN ({qs})",
                opportunity_ids,
            )
        ]

    print(f"listings matched    : {len(listing_keys)}")
    print(f"opportunities matched: {len(opportunity_ids)}")
    print(f"documents matched    : {len(doc_ids)}")

    if not (listing_keys or opportunity_ids or doc_ids):
        print("\nnothing to do -- already clean")
        conn.close()
        return 0

    if not args.apply:
        print("\nDRY RUN. re-run with --apply to delete.")
        conn.close()
        return 0

    conn.execute("PRAGMA foreign_keys = OFF")
    if doc_ids:
        qs = ",".join("?" * len(doc_ids))
        conn.execute(f"DELETE FROM documents WHERE document_id IN ({qs})", doc_ids)
    if opportunity_ids:
        qs = ",".join("?" * len(opportunity_ids))
        conn.execute(f"DELETE FROM opportunities WHERE id IN ({qs})", opportunity_ids)
    if listing_keys:
        qs = ",".join("?" * len(listing_keys))
        conn.execute(f"DELETE FROM listings WHERE key IN ({qs})", listing_keys)
    conn.commit()
    conn.close()

    print("\ndeleted. now re-run:")
    print("  python -c \"import repository; repository.sync_opportunities_from_listings(); print(repository.verify_projection())\"")
    print("  python export_static.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())