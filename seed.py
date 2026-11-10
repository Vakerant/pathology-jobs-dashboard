"""
Seed the database with concrete, verified pathology opportunities found during
research (June 2026). These give the dashboard immediate value before the first
live scrape. They are tagged is_seed=1 and carry a helpful snippet.

Re-running is safe: upsert is idempotent on (source_id, url, title).
"""
import db

# No placeholder listings. The entries that used to live here were hand-written
# summaries ("Senior Resident (Pathology) - AIIMS Bhopal (online application
# cycles)") rather than real notices: they never closed, carried no date, and
# sat at the top of the dashboard looking exactly like live vacancies. Every
# listing now comes from a scraped notice. run() is kept because CI and
# run_daily.sh still call `python seed.py`; scraper.cleanup_stored() deletes
# any seed rows an older database still holds.
SEEDS = []
# NOTE: the private diagnostic chains (Dr Lal PathLabs, Metropolis, Agilus/SRL)
# used to be seeded here. They were removed on purpose -- this tracker is for
# MD (Human) Pathology posts in govt/INI institutions, and a diagnostic chain's
# "Consultant Pathologist" is a different market with different pay bands.
# Leaving them in SEEDS was actively harmful: run_daily.sh runs seed.py before
# every scrape, so they were resurrected into the DB on every single pipeline
# run even though the scraper had stopped tracking them. tests/test_seed_alignment.py
# locks the invariant that every seeded source_id must still exist in sources.SOURCES.


def run():
    db.init_db()
    # One batch -> one connection, one transaction (21 separate connections and
    # commits is pure overhead for a fixed, hand-verified list).
    rows = [dict(s, is_seed=1) for s in SEEDS]
    db.upsert_listings(rows)
    print(f"Seeded {len(SEEDS)} verified opportunities.")


if __name__ == "__main__":
    import config
    config.setup_logging()
    run()
