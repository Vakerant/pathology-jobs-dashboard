"""Regression guards for bugs found in the Sep-2026 audit.

Each test here encodes a defect that was reproduced against the live code, so a
regression fails loudly instead of silently degrading operator-facing output.
"""
import re

import config
import scraper


# ---------------------------------------------------------------------------
# F1 — _pathology_sentence sliced mid-word
# ---------------------------------------------------------------------------
def test_pathology_sentence_starts_on_a_word_boundary():
    """A snippet must never begin partway through a word.

    The implementation backed the window off by ``best - 40`` and then added 1
    to "snap to a word boundary", which only ever moves the cut forward by a
    single character. With "Microbiology" straddling the boundary the result
    began "biology, and finally ...".
    """
    body = ("Applications invited for Senior Resident posts in Anatomy, "
            "Physiology, Community Medicine, Pharmacology, Microbiology, and "
            "finally the Department of Pathology.")
    out = scraper._pathology_sentence(body)
    assert out, "expected a snippet for a body that names pathology"
    first = out.split()[0]
    assert re.search(rf"\b{re.escape(first)}\b", body), (
        f"snippet starts mid-word: {first!r} is not a whole word in the body "
        f"(snippet={out!r})"
    )


def test_pathology_sentence_returns_none_without_pathology():
    assert scraper._pathology_sentence("Nothing relevant here at all.") is None
    assert scraper._pathology_sentence("") is None
    assert scraper._pathology_sentence(None) is None


def test_pathology_sentence_handles_keyword_at_offset_zero():
    """A body that *starts* with the keyword must not lose its first word."""
    body = "Pathology department posts are open for Senior Residents."
    out = scraper._pathology_sentence(body)
    assert out
    assert out.split()[0] in body.split() or out.startswith("Pathology")


# ---------------------------------------------------------------------------
# F2 — importing `app` fired a real network scrape
# ---------------------------------------------------------------------------
def test_auto_scrape_is_disabled_under_pytest():
    """Importing app.py must not start outbound scrapes during a test run.

    conftest.py sets AUTO_SCRAPE_ENABLED=0 before any test module imports app,
    so the background loop stays off. Previously `_ensure_auto_loop()` ran
    unconditionally at import and a bare `import app` spawned a thread pool that
    scraped every configured govt site — contradicting the workflow's claim
    that the test job is hermetic and network-free.
    """
    assert config.AUTO_SCRAPE_ENABLED is False, (
        "AUTO_SCRAPE_ENABLED must be False during tests; conftest.py sets the "
        "env var before app is imported"
    )


def test_auto_loop_did_not_start_on_import():
    import app

    assert app._auto_loop_started is False, (
        "the auto-scrape loop started at import time in the test process"
    )


def test_auto_scrape_flag_is_configurable():
    """The opt-out must be a real config knob, not a hardcoded test hack."""
    assert hasattr(config, "AUTO_SCRAPE_ENABLED")
    assert isinstance(config.AUTO_SCRAPE_ENABLED, bool)


# ---------------------------------------------------------------------------
# F3/F4 — CI must not publish from a pull request, nor on every branch push
# ---------------------------------------------------------------------------
def _workflow_text() -> str:
    import pathlib

    path = (pathlib.Path(__file__).resolve().parent.parent
            / ".github/workflows/daily-scrape.yml")
    return path.read_text()


def test_publish_job_does_not_run_on_pull_request():
    """`actions/checkout` leaves a PR checkout in detached HEAD, where the
    `git push` publishing step fails with "not currently on a branch" — so
    every PR showed a red scrape job."""
    text = _workflow_text()
    job = text.split("\n  scrape:", 1)
    assert len(job) == 2, "expected a `scrape:` job in the workflow"
    head = job[1].split("\n  #", 1)[0]
    cond = re.search(r"^\s{4}if:\s*(.+)$", head, re.M)
    assert cond, "the scrape (publish) job has no `if:` guard"
    assert "pull_request" in cond.group(1), (
        f"scrape job must be gated off pull_request events, got {cond.group(1)!r}"
    )


def test_push_trigger_is_limited_to_the_default_branch():
    """`branches: ['**']` ran the full network scrape and pushed bot commits
    onto every contributor branch."""
    text = _workflow_text()
    branches = re.search(r"^\s{4}branches:\s*\[(.*?)\]\s*$", text, re.M)
    assert branches, "no push branches filter found"
    listed = {b.strip().strip("'\"") for b in branches.group(1).split(",") if b.strip()}
    assert "**" not in listed, "push trigger must not fan out to every branch"
    assert listed <= {"main", "master"}, f"unexpected push branches: {listed}"


def test_ci_byte_compiles_every_module():
    """compileall previously listed only 6 of the 10 top-level modules."""
    text = _workflow_text()
    step = re.search(r"python -m compileall[^\n]*", text)
    assert step, "no compileall step in CI"
    cmd = step.group(0)
    for module in ("alerts.py", "export_static.py", "seed.py", "sources.py"):
        assert module in cmd, f"{module} is not byte-compiled in CI"
