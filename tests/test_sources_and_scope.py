"""Guards for the source registry and the strict MD-pathology scope filter.

Two separate contracts are locked down here:

1. ``SOURCES`` must contain only sources we intend to track -- no private
   diagnostic chains, and no duplicate ids (a duplicate id silently makes the
   scraper report one institution's listings under another's name).
2. Relevance must be MD (Human) Pathology ONLY. A "Senior Resident in Oral
   Pathology" or "Veterinary Pathologist" post is a real recruitment notice and
   contains the substring ``patholog``, so a positive-only keyword test happily
   keeps it. The scope guard has to run BEFORE the positive test.
"""
import pytest

import scraper
import sources


# ---------------------------------------------------------------------------
# Source registry hygiene
# ---------------------------------------------------------------------------
PRIVATE_LAB_IDS = ["lalpathlabs", "metropolis", "agilus"]


def test_private_diagnostic_chains_are_not_tracked():
    """Private labs were dropped deliberately -- they dominate the result count
    while carrying no govt MD-pathology openings."""
    ids = {s["id"] for s in sources.SOURCES}
    for lab in PRIVATE_LAB_IDS:
        assert lab not in ids, f"private source {lab!r} is still in SOURCES"


def test_source_ids_are_unique():
    """A duplicated id makes two institutions collapse into one row, splitting
    the listing count and corrupting the per-source stats."""
    ids = [s["id"] for s in sources.SOURCES]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate source ids: {sorted(dupes)}"


def test_every_source_has_the_required_fields():
    required = {"id", "name", "region", "category", "url"}
    for src in sources.SOURCES:
        missing = required - set(src)
        assert not missing, f"source {src.get('id')!r} is missing {sorted(missing)}"
        assert src["url"].startswith("http"), (
            f"source {src['id']!r} has a non-http url: {src['url']!r}"
        )


def test_ini_and_fellowship_coverage_is_present():
    """All-India AIIMS/INI colleges plus paid fellowships were added on request;
    keep at least the newly-covered institutions pinned so a later edit cannot
    silently drop them back to the old narrow set."""
    ids = {s["id"] for s in sources.SOURCES}
    expected = [
        "aiims_raipur", "aiims_nagpur", "aiims_guwahati",
        "aiims_raebareli", "aiims_deoghar", "aiims_rishikesh",
        "nib_genomics", "icmr",
        "uk_medical_education", "uk_health", "punjab_health",
    ]
    missing = [i for i in expected if i not in ids]
    assert not missing, f"expected sources missing from SOURCES: {missing}"


def test_aiims_rishikesh_points_at_the_job_page():
    """The old recruitment.html path yielded zero listings; job-new.php is the
    page that actually lists SR vacancies."""
    src = next(s for s in sources.SOURCES if s["id"] == "aiims_rishikesh")
    assert src["url"].endswith("/job-new.php"), (
        f"aiims_rishikesh url regressed to the empty page: {src['url']}"
    )


# ---------------------------------------------------------------------------
# Strict scope: MD (Human) Pathology only
# ---------------------------------------------------------------------------
OFF_SCOPE_TEXTS = [
    "Senior Resident in Oral Pathology at Govt Medical College",
    "Recruitment of MDS in Oral Medicine and Radiology",
    "Vacancy for Dental Surgeon, Oral & Maxillofacial Surgery Department",
    "Walk-in interview for Senior Resident in Veterinary Pathology, College of"
    " Veterinary Science",
    "Consultant Pathologist (Animal Health) - Animal Pathology Post",
]


@pytest.mark.parametrize("text", OFF_SCOPE_TEXTS)
def test_relevance_rejects_non_md_pathology(text):
    assert scraper.relevance(text) is None, (
        f"off-scope notice was scored as MD pathology: {text!r}"
    )


@pytest.mark.parametrize("text", OFF_SCOPE_TEXTS)
def test_mentions_pathology_rejects_non_md_pathology(text):
    """PDF bodies go through _mentions_pathology (no negative-keyword gate), so
    it needs the scope check independently."""
    assert scraper._mentions_pathology(text) is False


@pytest.mark.parametrize("text", OFF_SCOPE_TEXTS)
def test_pathology_sentence_rejects_non_md_pathology(text):
    assert scraper._pathology_sentence(text) is None, (
        f"snippet extracted from an off-scope document: {text!r}"
    )


IN_SCOPE_TEXTS = [
    "Senior Resident (Pathology) at AIIMS Bhopal",
    "Walk-in interview for Senior Resident in Pathology, Department of"
    " Pathology, AIIMS Nagpur",
    "Advertisement for Senior Resident in Neuropathology, NIMHANS",
    "Fellowship in Pathology (Histopathology), ACTREC Tata Memorial Centre",
]


@pytest.mark.parametrize("text", IN_SCOPE_TEXTS)
def test_genuine_md_pathology_is_still_relevant(text):
    assert scraper._mentions_pathology(text) is True
    assert scraper._pathology_sentence(text), (
        f"genuine MD-pathology notice lost its snippet: {text!r}"
    )


def test_scope_guard_runs_before_the_positive_keyword_test():
    """This is the ordering bug the guard exists to prevent: 'oral pathologist'
    and 'veterinary pathologist' both contain 'patholog'."""
    assert "oral patholog" in sources.OFF_SCOPE_PATHS
    assert "veterinar" in sources.OFF_SCOPE_PATHS
    assert scraper._off_scope("Oral Pathologist"), "off-scope helper missed 'oral pathologist'"
    assert not scraper._off_scope("Department of Pathology, AIIMS Bhopal")