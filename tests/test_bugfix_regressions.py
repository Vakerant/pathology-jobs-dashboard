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


# ---------------------------------------------------------------------------
# F9 — scrape_runs / source_runs were dead schema
#
# migrations.py created both tables, but nothing ever wrote a row. A scrape run
# left no auditable history, so "when did this last run and what did each source
# return" was unanswerable. `meta` held only the aggregate counters, which any
# ad-hoc single-source scrape silently overwrites.
# ---------------------------------------------------------------------------
def test_run_history_round_trips(tmp_path, monkeypatch):
    """A run must be openable before the work starts and closable after."""
    import db

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "runs.db"))
    db.init_db()

    rid = "run-unit-test"
    db.record_run_start(rid, "2026-10-01T00:00:00+00:00", 48)
    db.record_source_run(rid, "natboard", "failed", 403, 0, "HTTP 403")
    db.record_source_run(rid, "aiims_bhopal", "ok", 200, 12, "")
    db.record_run_finish(
        rid, "2026-10-01T00:01:00+00:00", "partial", 48, 47, 1, 530
    )

    conn = db.get_conn()
    row = conn.execute(
        "SELECT status, total_sources, ok, failed, new_items FROM scrape_runs"
        " WHERE run_id=?", (rid,)
    ).fetchone()
    assert tuple(row) == ("partial", 48, 47, 1, 530), tuple(row)

    per_source = dict(
        conn.execute(
            "SELECT source_id, status FROM source_runs WHERE run_id=?", (rid,)
        ).fetchall()
    )
    assert per_source == {"natboard": "failed", "aiims_bhopal": "ok"}, per_source
    conn.close()


def test_run_history_survives_a_crashed_run(tmp_path, monkeypatch):
    """`record_run_start` must write a 'running' row up front.

    If the row were only written at the end, a run killed mid-scrape would
    leave no trace at all — the exact failure the audit surfaced.
    """
    import db

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "crash.db"))
    db.init_db()

    db.record_run_start("run-crashed", "2026-10-01T00:00:00+00:00", 48)
    # deliberately no record_run_finish -- simulates a kill
    conn = db.get_conn()
    row = conn.execute(
        "SELECT status, finished_at FROM scrape_runs WHERE run_id='run-crashed'"
    ).fetchone()
    conn.close()
    assert row["status"] == "running"
    assert row["finished_at"] is None, "an unfinished run must not claim a finish time"


def test_one_raising_source_cannot_abort_the_run(tmp_path, monkeypatch):
    """A source that raises must not kill the other 47 or strand the run row.

    `scrape_source` handles its own errors, but an unforeseen exception
    (decode error, driver fault) would previously escape `run()`, leaving the
    `scrape_runs` row stuck at status='running' forever — the precise
    traceability gap the run-history audit was meant to close.
    """
    import db
    import scraper

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "boom.db"))
    db.init_db()

    def fake_scrape(src, session=None):
        if src["id"] == "explodes":
            raise RuntimeError("unexpected decoder fault")
        return (1, None)

    monkeypatch.setattr(scraper, "scrape_source", fake_scrape)
    monkeypatch.setattr(scraper, "SOURCES", [
        {"id": "explodes", "name": "Explodes", "region": "R", "category": "C", "url": "https://x"},
        {"id": "fine", "name": "Fine", "region": "R", "category": "C", "url": "https://y"},
    ])
    monkeypatch.setattr(scraper, "db", db)
    monkeypatch.setattr(scraper, "MAX_WORKERS", 2)

    scraper.run(verbose=False)

    conn = db.get_conn()
    run_row = conn.execute("SELECT status, ok, failed FROM scrape_runs").fetchone()
    per_source = {
        r["source_id"]: r["status"]
        for r in conn.execute("SELECT source_id, status FROM source_runs")
    }
    conn.close()

    assert run_row["status"] == "partial", run_row["status"]
    assert (run_row["ok"], run_row["failed"]) == (1, 1), tuple(run_row)
    # The healthy source must still have been recorded as ok.
    assert per_source == {"explodes": "failed", "fine": "ok"}, per_source
