"""seed.py must never reintroduce a source the scraper has stopped tracking.

Why this file exists
--------------------
`run_daily.sh` runs `seed.py` as its FIRST step, before every scrape. seed.py
unconditionally upserts every row in `SEEDS`. So any seed whose `source_id` is
not in `sources.SOURCES` is not a harmless leftover -- it is actively injected
into the live DB on every single pipeline run, even though nothing is scraping
it any more.

That is exactly how it happened: the private diagnostic chains (Dr Lal
PathLabs, Metropolis, Agilus/SRL) were removed from `sources.SOURCES` on
purpose, `purge_removed_sources.py` correctly deleted their rows, and then the
next daily run quietly put all 15 of them back. The purge kept "succeeding"
because the purge was never the thing at fault.

Note that `_orphaned_source_ids()` in purge_removed_sources.py could not have
caught this: seeds create no `source_status` row, so they are invisible to any
check derived from `source_status`. This test is derived from `sources.SOURCES`
instead, which is the actual registry of what is supposed to be tracked.
"""

import pytest

import seed as seed_module
import sources as source_registry

SEED_REQUIRED_FIELDS = {"source_id", "source_name", "region", "category", "title", "url"}

# The private diagnostic chains, retired deliberately. Kept as an explicit
# list so that reintroducing one fails on a message that says *why*, rather than
# on a generic "seed id not in SOURCES" failure.
RETIRED_PRIVATE_LAB_IDS = {"lalpathlabs", "metropolis", "agilus"}
RETIRED_PRIVATE_REGIONS = {"Private / Metro"}
RETIRED_PRIVATE_CATEGORIES = {"Private – Consultant Pathologist"}


def _seed_ids():
    return {s["source_id"] for s in seed_module.SEEDS}


def test_seed_ids_are_unique():
    ids = [s["source_id"] for s in seed_module.SEEDS]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate seed source_id(s): {sorted(dupes)}"


def test_seeds_are_not_empty():
    # Guards against a botched edit silently emptying the seed set.
    assert len(seed_module.SEEDS) >= 15, f"only {len(seed_module.SEEDS)} seeds left"


def test_every_seed_has_the_required_fields():
    for s in seed_module.SEEDS:
        missing = SEED_REQUIRED_FIELDS - set(s)
        assert not missing, f"seed {s.get('source_id')!r} missing {sorted(missing)}"
        assert s["url"].startswith("http"), f"seed {s['source_id']!r} has bad url {s['url']!r}"


def test_retired_private_labs_are_not_seeded():
    """The regression that motivated this file.

    Verified non-vacuously: with the three entries restored to SEEDS this
    fails, because 'metropolis' (etc.) is not a key in sources.SOURCES.
    """
    seeded = _seed_ids()
    leaked = RETIRED_PRIVATE_LAB_IDS & seeded
    assert not leaked, (
        f"seed.py is re-seeding retired private labs {sorted(leaked)}. run_daily.sh "
        "runs seed.py before every scrape, so these come back into the live DB "
        "on every daily run even though nothing scrapes them any more."
    )


def test_no_seed_uses_a_retired_private_region_or_category():
    for s in seed_module.SEEDS:
        assert s["region"] not in RETIRED_PRIVATE_REGIONS, (
            f"seed {s['source_id']!r} still uses retired region {s['region']!r}"
        )
        assert s["category"] not in RETIRED_PRIVATE_CATEGORIES, (
            f"seed {s['source_id']!r} still uses retired category {s['category']!r}"
        )


def test_every_seeded_source_is_still_a_configured_source():
    """The core invariant.

    A seed for an un-configured source is indistinguishable, in the DB, from a
    live listing -- so nothing downstream can filter it out. It has to be
    prevented at the definition site.
    """
    configured = {s["id"] for s in source_registry.SOURCES}
    orphaned = sorted(_seed_ids() - configured)
    assert not orphaned, (
        f"seeded source_id(s) absent from sources.SOURCES: {orphaned}. "
        "Either remove the seed or re-add the source."
    )


@pytest.mark.parametrize("source_id", sorted(RETIRED_PRIVATE_LAB_IDS))
def test_retired_private_labs_are_absent_from_the_scraper_registry(source_id):
    assert source_id not in {s["id"] for s in source_registry.SOURCES}
