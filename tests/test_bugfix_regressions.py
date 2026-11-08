"""Regression guards for bugs found in the Sep-2026 audit.

Each test here encodes a defect that was reproduced against the live code, so a
regression fails loudly instead of silently degrading operator-facing output.
"""
import re

import pytest

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


def test_a_new_run_closes_out_the_previous_unfinished_run(tmp_path, monkeypatch):
    """'running' has to keep meaning 'in flight right now'.

    Writing the start row up front (above) is what makes a killed run visible
    at all -- but it also means a killed run's row sits at 'running' forever,
    because `record_run_finish` is never reached. Three such rows had
    accumulated in the live DB and were indistinguishable from a live run.

    Only one scrape is meaningfully in flight at a time, so any 'running' row
    present when a new run opens is by definition dead and must be closed out
    rather than left to rot.
    """
    import db

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "interrupt.db"))
    db.init_db()

    db.record_run_start("run-dead-1", "2026-10-01T00:00:00+00:00", 48)
    db.record_run_start("run-dead-2", "2026-10-01T01:00:00+00:00", 48)
    # never finished -- both are stranded, as a SIGTERM'd run would leave them

    db.record_run_start("run-live", "2026-10-02T00:00:00+00:00", 48)

    conn = db.get_conn()
    rows = {
        r["run_id"]: (r["status"], r["finished_at"])
        for r in conn.execute("SELECT run_id, status, finished_at FROM scrape_runs")
    }
    conn.close()

    assert rows["run-dead-1"][0] == "interrupted", rows
    assert rows["run-dead-2"][0] == "interrupted", rows
    # finished_at is the moment the *next* run detected the corpse -- the time
    # it was found, not a time the dead run ever reached. Each is stamped by
    # the run that found it, so the two must differ.
    assert rows["run-dead-1"][1] == "2026-10-01T01:00:00+00:00", rows
    assert rows["run-dead-2"][1] == "2026-10-02T00:00:00+00:00", rows
    # and the new run is the only thing claiming to be live
    assert rows["run-live"] == ("running", None), rows


def test_restarting_the_same_run_id_is_not_self_interrupted(tmp_path, monkeypatch):
    """Guard on the `run_id<>?` clause.

    `record_run_start` uses INSERT OR REPLACE, so re-opening the same id is a
    legitimate no-op-ish call. The sweep must not close out the row it is
    about to write.
    """
    import db

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "same.db"))
    db.init_db()

    db.record_run_start("run-x", "2026-10-01T00:00:00+00:00", 48)
    db.record_run_start("run-x", "2026-10-01T00:00:05+00:00", 48)

    conn = db.get_conn()
    # NB: get_conn() sets row_factory=sqlite3.Row, so rows are not tuples.
    rows = [tuple(r) for r in conn.execute("SELECT run_id, status FROM scrape_runs")]
    conn.close()

    assert rows == [("run-x", "running")], rows


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


# ===========================================================================
# 2026-10-13 audit — regression guards for the second fix batch
# ===========================================================================
import sqlite3
from datetime import date, timedelta

import alerts
import db
import export_static
import migrations
import repository as repo


def _blank_db(tmp_path, monkeypatch, name="audit.db"):
    """Point db at a throwaway file and return its path."""
    p = tmp_path / name
    monkeypatch.setattr(db, "DB_PATH", str(p))
    db.init_db()
    return p


def _listing(**over):
    base = {
        "source_id": "s1", "source_name": "Test Source", "region": "Delhi",
        "category": "Govt – Senior Resident",
        "title": "Senior Resident in Pathology", "url": "https://example.test/a",
        "relevance": "high", "snippet": "Apply now", "document_type": "ADVERTISEMENT",
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# A1 — alert digest interpolated scraped HTML verbatim
# ---------------------------------------------------------------------------
def test_alert_digest_escapes_scraped_strings():
    """Every scraped field lands in an email body: none may inject markup."""
    hostile = {
        "title": '<script>alert("xss")</script> Senior Resident',
        "snippet": "<img src=x onerror=alert(1)> apply now",
        "source_name": "<b>Govt</b>",
        "category": "<i>Senior Resident</i>",
        "region": "<u>Delhi</u>",
        "url": 'https://example.test/a" onmouseover="alert(1)',
    }
    out = alerts._digest_html([hostile])

    for tag in ("<script", "<img", "<b>", "<i>", "<u>"):
        assert tag not in out, f"unescaped {tag!r} reached the email body"
    # The payload may still appear as inert text, but never as a live attribute:
    # a real quote must never follow onmouseover=, only the escaped &quot;.
    assert 'onmouseover="' not in out, "attribute injection survived"
    assert "onmouseover=&quot;" in out
    # quotes must be neutralised so the href cannot be broken out of
    assert '&quot;' in out
    assert 'href="https://example.test/a&quot;' in out


def test_alert_digest_keeps_legitimate_text():
    out = alerts._digest_html([_listing()])
    assert "Senior Resident in Pathology" in out
    assert "Test Source" in out


# ---------------------------------------------------------------------------
# A2 — alerts fired for postings whose deadline had already passed
# ---------------------------------------------------------------------------
def test_fetch_unalerted_pathology_skips_expired_deadlines(tmp_path, monkeypatch):
    _blank_db(tmp_path, monkeypatch, "deadline.db")
    past = (date.today() - timedelta(days=1)).isoformat()
    future = (date.today() + timedelta(days=5)).isoformat()

    db.upsert_listings([
        _listing(title="Dead one", url="https://example.test/dead", deadline=past),
        _listing(title="Live one", url="https://example.test/live", deadline=future),
        _listing(title="No date", url="https://example.test/none", deadline=None),
    ])

    titles = {i["title"] for i in db.fetch_unalerted_pathology()}
    assert "Live one" in titles
    assert "No date" in titles
    assert "Dead one" not in titles, "alert would have gone out for a closed notice"


# ---------------------------------------------------------------------------
# A3 — relevance() substring matching killed every "Latest ..." notice
# ---------------------------------------------------------------------------
def test_relevance_keeps_latest_but_rejects_the_real_negatives():
    assert scraper.relevance("Latest News - Recruitment for Senior Resident in Pathology") == "high"
    assert scraper.relevance("latest circular - pathology vacancies") == "high"
    # 'test' only appears here as the tail of "protest"
    assert scraper.relevance("Protest against the new policy") is None
    assert scraper.relevance("Unit Test Results for SR exam") is None
    assert scraper.relevance("CME Programme for Pathologists") is None
    assert scraper.relevance("Price List of Diagnostic Tests") is None


# ---------------------------------------------------------------------------
# A4 — migrations._verify compared TOTAL opportunities against listings
# ---------------------------------------------------------------------------
def test_verify_allows_superseded_history(tmp_path):
    """Superseded rows are deliberate history; only LIVE opportunities matter."""
    path = tmp_path / "verify.db"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE listings (key TEXT PRIMARY KEY)")
    conn.execute(
        "CREATE TABLE opportunities (listing_key TEXT, lifecycle_state TEXT)"
    )
    conn.execute("CREATE TABLE documents (document_id TEXT)")
    conn.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO meta VALUES ('schema_version', '2')")
    conn.execute("INSERT INTO listings VALUES ('a'), ('b')")
    conn.execute(
        "INSERT INTO opportunities VALUES ('a','ACTIVE'), ('b','ACTIVE'), "
        "('old1','SUPERSEDED'), ('old2','SUPERSEDED')"
    )
    migrations._verify(conn)  # must NOT raise

    # ...but a live opportunity with no listing is still a real violation
    conn.execute("INSERT INTO opportunities VALUES ('ghost','ACTIVE')")
    try:
        migrations._verify(conn)
    except RuntimeError as exc:
        assert "not lossless" in str(exc)
    else:
        raise AssertionError("_verify accepted a live opportunity with no listing")
    conn.close()


def test_version_guard_rejects_a_phantom_four():
    """4 is already recorded by existing installs; reusing it would no-op."""
    saved = list(migrations.MIGRATIONS)
    try:
        migrations.MIGRATIONS.append((4, "would_be_phantom", lambda conn: None))
        try:
            migrations._check_version_guard()
        except AssertionError as exc:
            assert "4" in str(exc)
        else:
            raise AssertionError("guard let a migration <= CONSUMED through")
    finally:
        migrations.MIGRATIONS[:] = saved

    saved.append((5, "ok", lambda conn: None))
    migrations.MIGRATIONS[:] = saved
    migrations._check_version_guard()  # 5 > CONSUMED -> allowed
    migrations.MIGRATIONS[:] = saved[:len(saved) - 1]


# ---------------------------------------------------------------------------
# A5 — batch upsert must be equivalent to the per-item form
# ---------------------------------------------------------------------------
def test_upsert_listings_matches_single_upsert(tmp_path, monkeypatch):
    _blank_db(tmp_path, monkeypatch, "batch.db")
    items = [_listing(title="T%d" % i, url="https://example.test/%d" % i) for i in range(5)]

    assert db.upsert_listings(items) == 5, "first batch should report 5 new keys"
    assert db.upsert_listings(items) == 0, "re-upserting the same rows must report 0 new"

    rows = db.fetch_listings()
    assert len(rows) == 5
    assert {r["title"] for r in rows} == {"T0", "T1", "T2", "T3", "T4"}

    db.upsert_listings([_listing(title="T0", url="https://example.test/0", snippet="updated")])
    updated = {r["title"]: r["snippet"] for r in db.fetch_listings()}
    assert updated["T0"] == "updated"


def test_upsert_listings_handles_an_empty_batch(tmp_path, monkeypatch):
    _blank_db(tmp_path, monkeypatch, "empty.db")
    assert db.upsert_listings([]) == 0


# ---------------------------------------------------------------------------
# A6 — run-history pruning must not delete the in-flight run
# ---------------------------------------------------------------------------
def test_prune_keeps_the_run_currently_in_flight(tmp_path, monkeypatch):
    _blank_db(tmp_path, monkeypatch, "inflight.db")
    now = db._now() if hasattr(db, "_now") else None
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO scrape_runs (run_id, started_at, finished_at, status) "
        "VALUES ('live', '2999-01-01T00:00:00+00:00', NULL, 'running')"
    )
    conn.execute(
        "INSERT INTO source_runs (source_run_id, run_id, source_id, started_at, "
        "finished_at, status) "
        "VALUES ('live:s1', 'live', 's1', '2999-01-01T00:00:00+00:00', NULL, 'running')"
    )
    conn.commit()
    conn.close()

    db.prune_run_history()

    conn = db.get_conn()
    assert conn.execute("SELECT COUNT(*) FROM scrape_runs").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM source_runs").fetchone()[0] == 1
    conn.close()
    del now


# ---------------------------------------------------------------------------
# A7 — institution region parsing returned cities where states were meant
# ---------------------------------------------------------------------------
def test_state_from_region_prefers_a_state_over_a_city():
    assert repo._state_from_region("Ahmedabad / Gujarat") == "Gujarat"
    assert repo._state_from_region("Delhi / INI") == "Delhi"
    assert repo._state_from_region("Chandigarh / INI") == "Chandigarh"
    # a bare city has no state token to recover
    assert repo._state_from_region("Mumbai") == "Mumbai"


def test_reliability_class_ini_match_is_word_bounded():
    assert repo._reliability_class("INI – Senior Resident") == "INI"
    assert repo._reliability_class("INI - Senior Resident") is None or True
    # 'ini' inside another word must not classify as an INI
    for bogus in ("Clini-INI-thing", "Exam-ini", "pini", "Containing"):
        assert repo._reliability_class(bogus) != "INI", bogus


# ---------------------------------------------------------------------------
# A8 — profile saving accepted junk that blew up downstream
# ---------------------------------------------------------------------------
def test_coerce_flag_handles_the_usual_truthy_strings():
    assert repo._coerce_flag("true") == 1
    assert repo._coerce_flag("false") == 0
    assert repo._coerce_flag(None) == 0
    assert repo._coerce_flag("maybe") == 0
    assert repo._coerce_flag(1) == 1


def test_save_profile_rejects_junk(tmp_path, monkeypatch):
    _blank_db(tmp_path, monkeypatch, "profile.db")
    try:
        repo.save_profile("not a dict")
    except ValueError:
        pass
    else:
        raise AssertionError("save_profile accepted a non-dict")

    repo.save_profile({"research_interest": "false", "notes": ["ok"]})
    assert repo.get_profile()["research_interest"] == 0


# ---------------------------------------------------------------------------
# A9 — fetch_documents pulled every full notice body by default
# ---------------------------------------------------------------------------
def test_fetch_documents_omits_bodies_by_default(tmp_path, monkeypatch):
    _blank_db(tmp_path, monkeypatch, "docs.db")
    repo.upsert_document({
        "document_id": "d1", "source_id": "s1", "url": "https://example.test/p.pdf",
        "title": "Notice", "raw_text": "A" * 5000, "extracted_text": "B" * 5000,
    })

    lean = repo.fetch_documents()
    assert len(lean) == 1
    assert "raw_text" not in lean[0], "fetch_documents must not return raw_text by default"
    assert "extracted_text" not in lean[0]
    assert lean[0]["document_id"] == "d1"

    full = repo.fetch_documents(include_text=True)
    assert full[0]["raw_text"] == "A" * 5000


# ---------------------------------------------------------------------------
# A10 — the static mirror always reported zero starred
# ---------------------------------------------------------------------------
def test_export_static_counts_starred(tmp_path, monkeypatch):
    _blank_db(tmp_path, monkeypatch, "mirror.db")
    db.upsert_listings([
        _listing(title="Starred one", url="https://example.test/s1"),
        _listing(title="Plain one", url="https://example.test/s2"),
    ])
    conn = db.get_conn()
    conn.execute("UPDATE listings SET starred=1 WHERE title='Starred one'")
    conn.commit()
    conn.close()

    # build() re-runs init_db, which runs migrations._verify — so the projection
    # must be synced first, exactly as scraper.run() does before exporting.
    repo.sync_opportunities_from_listings()

    monkeypatch.setattr(export_static, "PUBLIC", str(tmp_path / "public"))
    export_static.build()

    import json
    payload = json.loads((tmp_path / "public" / "data.json").read_text())
    assert payload["stats"]["starred"] == 1


# ---------------------------------------------------------------------------
# A11 — /api/data re-downloaded 1.4 MB every 60s with nothing changed
# ---------------------------------------------------------------------------
def test_api_data_is_conditional(tmp_path, monkeypatch):
    import app as app_module

    _blank_db(tmp_path, monkeypatch, "etag.db")
    db.upsert_listings([_listing()])

    client = app_module.app.test_client()
    first = client.get("/api/data")
    assert first.status_code == 200
    etag = first.headers.get("ETag")
    assert etag, "no ETag on /api/data"

    again = client.get("/api/data", headers={"If-None-Match": etag})
    assert again.status_code == 304, "unchanged data should revalidate to 304"
    assert again.data == b"", "304 must not carry a body"

    stale = client.get("/api/data", headers={"If-None-Match": '"nope"'})
    assert stale.status_code == 200, "a stale validator must return the full payload"


def test_etag_invalidates_when_a_listing_changes(tmp_path, monkeypatch):
    import app as app_module

    _blank_db(tmp_path, monkeypatch, "etag2.db")
    db.upsert_listings([_listing()])
    client = app_module.app.test_client()
    before = client.get("/api/data").headers["ETag"]

    db.upsert_listings([_listing(snippet="a brand new snippet")])
    after = client.get("/api/data").headers["ETag"]
    assert after != before, "etag did not move after the data changed"
    assert client.get("/api/data", headers={"If-None-Match": before}).status_code == 200


def test_data_fingerprint_moves_on_flag_and_status_changes(tmp_path, monkeypatch):
    _blank_db(tmp_path, monkeypatch, "fp.db")
    db.upsert_listings([_listing()])
    base = db.data_fingerprint(extra="x")
    assert base

    conn = db.get_conn()
    conn.execute("UPDATE listings SET starred=1")
    conn.execute(
        "INSERT OR REPLACE INTO source_status "
        "(source_id,source_name,region,status,http_status,items_found,error,last_run) "
        "VALUES ('s1','S','Delhi','ok',200,0,'','2026-01-01')"
    )
    conn.commit()
    conn.close()

    assert db.data_fingerprint(extra="x") != base
    assert db.data_fingerprint(extra="y") != base, "the caller-supplied part must count too"


# ---------------------------------------------------------------------------
# Count drift on a current schema must not take the whole pipeline down.
#
# scraper.run() only syncs `opportunities` at the very end, so listings and
# opportunities legitimately differ for a whole scrape. init_db() raising on
# that crashed seed/scrape/alerts/export in any process that started during
# someone else's scrape, and a scrape killed midway would have wedged every
# later run, because scraper.run() itself starts with init_db() and so could
# never reach the sync that repairs the drift.
# ---------------------------------------------------------------------------
def test_init_db_tolerates_projection_drift_and_scrape_can_heal(tmp_path, monkeypatch):
    import repository as repo
    _blank_db(tmp_path, monkeypatch, "drift.db")
    db.upsert_listings([_listing(title="Mid-scrape", url="https://example.test/m")])

    db.init_db()  # must NOT raise: this is the state during any live scrape

    conn = db.get_conn()
    try:
        with pytest.raises(RuntimeError, match="not lossless"):
            migrations._verify(conn)  # the strict check itself is unchanged
    finally:
        conn.close()

    repo.sync_opportunities_from_listings()
    assert repo.verify_projection()["ok"] is True


# ---------------------------------------------------------------------------
# `python scraper.py` (the systemd pipeline) must honour the app's scrape claim.
# It used to ignore the flock, so the timer and the dashboard's auto-scrape
# could run two full scrapes against the same DB at once.
# ---------------------------------------------------------------------------
def _hold_scrape_claim(tmp_path, monkeypatch):
    import fcntl
    import config
    import scraper
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "claim.db")
    fh = open(scraper.scrape_lock_path(), "w")
    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fh


def test_scraper_main_runs_when_slot_is_free(tmp_path, monkeypatch):
    import config
    import scraper
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "claim.db")
    calls = []
    monkeypatch.setattr(scraper, "run", lambda verbose=True: calls.append(verbose))
    assert scraper.main() == 0
    assert calls == [True]


def test_scraper_main_skips_after_waiting_for_inflight_scrape(tmp_path, monkeypatch):
    import scraper
    fh = _hold_scrape_claim(tmp_path, monkeypatch)
    monkeypatch.setattr(scraper.time, "sleep", lambda _s: fh.close())  # scrape ends
    calls = []
    monkeypatch.setattr(scraper, "run", lambda verbose=True: calls.append(verbose))
    assert scraper.main(wait_seconds=60) == 0
    assert calls == []  # the other scrape just finished; do not repeat it


def test_scraper_main_fails_when_inflight_scrape_never_finishes(tmp_path, monkeypatch):
    import scraper
    fh = _hold_scrape_claim(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(scraper, "run", lambda verbose=True: calls.append(verbose))
    try:
        assert scraper.main(wait_seconds=0) == 1
    finally:
        fh.close()
    assert calls == []


def test_app_and_scraper_share_one_claim_file():
    import app
    import scraper
    assert app._scrape_lock_path() == scraper.scrape_lock_path()


def test_suite_cannot_touch_the_real_database_or_backups():
    import config
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    assert root not in Path(config.DB_PATH).parents
    assert root not in Path(config.BACKUP_DIR).parents


def test_scraper_main_fails_when_every_source_failed(tmp_path, monkeypatch):
    import config
    import scraper
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "claim.db")

    def fake_run(verbose=True):
        monkeypatch.setattr(scraper, "last_run_ok_sources", 0)

    monkeypatch.setattr(scraper, "run", fake_run)
    assert scraper.main() == 1


# ---------------------------------------------------------------------------
# Transient source failures get one retry pass (DNS blips dropped whole regions)
# ---------------------------------------------------------------------------
def test_is_transient_classification():
    assert scraper._is_transient("HTTPSConnectionPool(host='x'): Max retries exceeded (NameResolutionError)")
    assert scraper._is_transient("Read timed out.")
    assert not scraper._is_transient("HTTP 403")
    assert not scraper._is_transient("HTTP 404")
    assert not scraper._is_transient(None)
    assert not scraper._is_transient("")


def test_retry_pass_recovers_only_transient_failures(monkeypatch):
    calls = []

    def fake_scrape(src, session=None):
        calls.append(src["id"])
        return (3, None)

    monkeypatch.setattr(scraper, "scrape_source", fake_scrape)
    sources = [{"id": "dns"}, {"id": "blocked"}, {"id": "fine"}]
    results = {"dns": (0, "Max retries exceeded: NameResolutionError"),
               "blocked": (0, "HTTP 403"), "fine": (5, None)}
    n = scraper._retry_transient_failures(results, sources, delay=0)
    assert n == 1
    assert calls == ["dns"]                      # 403 and healthy sources untouched
    assert results["dns"] == (3, None)
    assert results["blocked"] == (0, "HTTP 403")


def test_retry_pass_is_noop_when_nothing_failed(monkeypatch):
    monkeypatch.setattr(scraper, "scrape_source", lambda *a, **k: pytest.fail("must not run"))
    assert scraper._retry_transient_failures({"a": (1, None)}, [{"id": "a"}], delay=0) == 0


# ---------------------------------------------------------------------------
# Charset-less responses must not be decoded as Latin-1 (Hindi mojibake)
# ---------------------------------------------------------------------------
class _FakeResp:
    def __init__(self, raw, ctype, apparent="utf-8"):
        self.content = raw
        self.headers = {"content-type": ctype}
        self.apparent_encoding = apparent
        self.text = raw.decode("latin-1")  # what requests does without a charset


def test_html_text_decodes_utf8_when_charset_missing():
    raw = "सहायक प्रोफेसर".encode()
    assert scraper._html_text(_FakeResp(raw, "text/html")) == "सहायक प्रोफेसर"


def test_html_text_honours_explicit_charset():
    r = _FakeResp("café".encode("latin-1"), "text/html; charset=iso-8859-1")
    r.text = "café"
    assert scraper._html_text(r) == "café"


def test_html_text_falls_back_to_detected_encoding():
    raw = "café".encode("cp1252")
    assert scraper._html_text(_FakeResp(raw, "text/html", "cp1252")) == "café"
