"""Tests for the Flask app: scrape-slot atomicity, config wiring, shutdown.

The scrape slot guard used to be a check-then-set on a plain dict performed
*outside* the lock, with the lock released before the worker thread did any
work. Two concurrent ``POST /api/refresh`` calls could therefore both pass the
check and launch overlapping scrapes against the same SQLite file. These tests
pin the fixed behaviour.
"""
import pathlib
import threading
import time

import pytest

import app as A


@pytest.fixture()
def client(monkeypatch):
    """Test client with the scrape *work* stubbed — no network, no DB writes.

    ``_do_scrape`` itself must NOT be stubbed: its ``finally`` block is what
    releases the scrape slot, so replacing it would make the release path
    untestable. We stub ``scraper.run`` / ``alerts.send_new`` /
    ``export_static.build`` instead and let the real orchestration run.

    The per-IP rate limiter is also lifted here. This module asserts on the
    scrape slot, and the slot's contract is "one 202, everyone else 200 +
    started:false" across 12 concurrent callers — which is deliberately more
    calls per minute than ``config.REFRESH_RATE_LIMIT`` allows. Throttling is
    covered by tests/test_security_hardening.py; here it would only mask the
    race this file exists to detect.
    """
    A.app.config["TESTING"] = True
    monkeypatch.setattr(A, "_RATE_LIMITED_PATHS",
                        {"/api/refresh": 10_000, "/api/flag": 10_000})
    A._rate_limit_reset()
    monkeypatch.setattr(A.scraper, "run", lambda verbose=False: 0)
    monkeypatch.setattr(A.alerts, "send_new", lambda verbose=False: 0)
    monkeypatch.setattr(A.export_static, "build", lambda: None)
    # start from a known-idle slot so tests are order-independent
    with A._scrape_lock:
        A._scraping["running"] = False
    yield A.app.test_client()
    with A._scrape_lock:
        A._scraping["running"] = False


def test_config_is_not_duplicated():
    """app.py must read these from config.py, not hardcode them."""
    assert A.AUTO_SCRAPE_HOURS == A.config.AUTO_SCRAPE_HOURS
    assert A._KEY_RE is A.config.FLAG_KEY_RE


def test_shutdown_event_is_reusable():
    """The loop must wait on a signal-able Event, not a throwaway one."""
    assert isinstance(A._shutdown, threading.Event)
    assert A.AUTO_SCRAPE_POLL_SECONDS > 0


def test_concurrent_refresh_claims_slot_once(client):
    """Regression: 12 racing refreshes must yield exactly one scrape."""
    spawned = []
    barrier = threading.Barrier(12)

    def slow_run(verbose=False):
        spawned.append(1)
        time.sleep(0.3)

    # slow down the work, not the orchestration: the release path must still run
    A.scraper.run = slow_run

    results = []
    lock = threading.Lock()

    def hit():
        barrier.wait()
        resp = A.app.test_client().post("/api/refresh")
        with lock:
            results.append((resp.status_code, resp.get_json()))

    threads = [threading.Thread(target=hit) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 12
    started = [b for _, b in results if b.get("started")]
    assert len(started) == 1, f"race: {len(started)} scrapes started"
    assert len(spawned) == 1, f"worker ran {len(spawned)} times"
    # losers get 200 + started:false, winner gets 202
    codes = sorted(c for c, _ in results)
    assert codes.count(202) == 1
    assert codes.count(200) == 11


def test_slot_released_after_scrape_completes(client):
    """A finished scrape must free the slot so a later refresh can claim it."""
    assert client.post("/api/refresh").get_json()["started"] is True
    for _ in range(50):
        if not A._is_scraping():
            break
        time.sleep(0.02)
    assert A._is_scraping() is False
    assert client.post("/api/refresh").get_json()["started"] is True


def test_is_scraping_reads_under_lock(client):
    A._scraping["running"] = True
    assert A._is_scraping() is True
    A._scraping["running"] = False
    assert A._is_scraping() is False


def test_healthz_is_dependency_free(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json()["status"] in ("ok", "healthy", "alive")


def test_readyz_reports_last_run(client):
    resp = client.get("/readyz")
    assert resp.status_code in (200, 503)
    assert "status" in resp.get_json()


def test_refresh_ui_reports_scrape_outcome_and_cannot_hang():
    """Guard the observability fix in templates/dashboard.html.

    Three defects motivated this: an *unguarded* ``await fetch`` that left the
    button spinning forever on any network error, an *unbounded* poll that
    never gave up, and *no result shown at all* on success — so a real 51s
    scrape was indistinguishable from a silent no-op. Asserted loosely (string
    presence, not exact source) so ordinary refactors don't break the build.
    """
    import re
    tpl = pathlib.Path(__file__).resolve().parents[1] / "templates" / "dashboard.html"
    html = tpl.read_text(encoding="utf-8")

    m = re.search(r"async function refresh\s*\(\s*\)\s*\{", html)
    assert m, "refresh() not found in dashboard.html"
    body = html[m.end():m.end() + 3000]

    assert "scrape-result" in html, "missing #scrape-result status node"
    assert "try" in body and "catch" in body, "fetch('/api/refresh') must be guarded"
    assert "finally" in body, "button state must be restored in a finally block"
    # a hard deadline so a crashed scrape cannot leave a permanent spinner
    assert "180000" in body, "poll must be time-bounded"
    # every terminal path must report something to the user
    assert body.count("showScrapeResult(") >= 3, (
        "refresh() must report started / completed / error outcomes"
    )

    # the completion summary is a separate helper; it must read the new stats
    ms = re.search(r"function scrapeSummary\s*\([^)]*\)\s*\{", html)
    assert ms, "scrapeSummary() helper not found"
    summary = html[ms.end():ms.end() + 800]
    assert "last_run_new" in summary, "summary must report how many listings were new"
    assert "sources_ok" in summary and "sources_failed" in summary, (
        "summary must report source health"
    )
    assert "orphans" in summary, "summary must surface projection drift"


def test_api_data_stats_shape(client):
    data = client.get("/api/data").get_json()
    stats = data["stats"]
    for key in ("total", "new_week", "starred", "high", "sources_ok",
                "sources_failed", "last_run", "scraping",
                # scrape-outcome keys: without these the UI can only say
                # "Updating..." and never reports what the scrape did.
                "last_run_new", "last_run_ok", "last_run_failed",
                "projection_ok", "orphans"):
        assert key in stats, f"missing stats key: {key}"
    assert isinstance(data["listings"], list)
    for key in ("last_run_new", "last_run_ok", "last_run_failed", "orphans"):
        assert isinstance(stats[key], int), f"stats[{key}] must be an int"
    assert isinstance(stats["projection_ok"], bool)


@pytest.mark.parametrize("stored,expected", [
    ("7", 7),          # normal TEXT meta row written by db.set_meta
    ("0", 0),
    (None, 0),         # key absent on a fresh DB
    ("", 0),
    ("not-a-number", 0),  # must not raise into /api/data
])
def test_meta_int_coerces_safely(monkeypatch, stored, expected):
    """``meta`` rows are TEXT and may be missing or corrupt — never raise.

    Regression: /api/data 500s if a stray non-numeric meta value reaches
    ``int()``, which would take out the whole dashboard, not just the badge.
    """
    monkeypatch.setattr(A.db, "get_meta", lambda key, default=None: stored)
    assert A._meta_int("last_run_new") == expected


def test_api_data_survives_corrupt_meta(client, monkeypatch):
    """A garbage ``meta`` value must not 500 the whole dashboard.

    This is the real user-visible contract: ``/api/data`` is the single
    payload the UI renders from, so an exception while reading a scrape
    counter takes out the entire page, not just one badge.
    """
    garbage = {
        "last_run_new": "not-a-number",
        "last_run_ok": "",
        "last_run_failed": None,
        "last_run_orphans": "12",       # numeric TEXT -> must become int 12
    }
    real_get_meta = A.db.get_meta

    def fake_get_meta(key, default=None):
        return garbage.get(key, real_get_meta(key, default))

    monkeypatch.setattr(A.db, "get_meta", fake_get_meta)

    resp = client.get("/api/data")
    assert resp.status_code == 200
    stats = resp.get_json()["stats"]
    assert stats["last_run_new"] == 0        # unparseable -> 0, not a 500
    assert stats["last_run_ok"] == 0
    assert stats["last_run_failed"] == 0
    assert stats["orphans"] == 12            # numeric TEXT still parses


@pytest.mark.parametrize("payload", [
    {"key": "z" * 40, "field": "starred", "value": 1},      # unknown key
    {"key": "a" * 40, "field": "nope", "value": 1},         # field not allowlisted
    {"key": "a" * 40, "field": "starred", "value": "x"},    # bad value
    {"key": "short", "field": "starred", "value": 1},       # malformed key
])
def test_flag_rejects_bad_input(client, payload):
    assert client.post("/api/flag", json=payload).status_code in (400, 404)

# ---------------------------------------------------------------------------
# Multi-process safety (systemd runs `gunicorn --workers 2`, no --preload)
# ---------------------------------------------------------------------------
# Because there is no --preload, every worker imports this module and runs the
# startup block independently. The scrape-slot guard (_scrape_lock /
# _scraping) is per-process, so an in-process guard alone cannot stop two
# workers from each firing a scrape at the same SQLite file. _ensure_auto_loop
# therefore elects a single owner with an exclusive advisory lock.

_SIGTERM_PROBE = """
import signal
import app
# SIG_DFL is an IntEnum, so str() renders "0" — compare against the enum.
h = signal.getsignal(signal.SIGTERM)
print("is_default=" + str(h == signal.SIG_DFL), flush=True)
"""


def test_import_does_not_steal_sigterm_handler(tmp_path):
    """Importing the app must leave the process SIGTERM handler untouched.

    gunicorn imports the app in each worker's main thread, so an import-time
    `signal.signal(SIGTERM, ...)` would *succeed* and replace the handler
    gunicorn depends on for graceful exit. The worker would then ignore the
    signal and hang until SIGKILL, breaking `systemctl --user restart`.
    Asserted in a subprocess: after `import app` the handler is still the
    interpreter default, i.e. nothing was installed at import time.
    """
    import os
    import subprocess
    import sys

    env = dict(os.environ)
    env["DB_PATH"] = str(tmp_path / "data.db")
    env["BACKUP_DIR"] = str(tmp_path / "backups")
    env["PYTHONPATH"] = str(pathlib.Path(__file__).resolve().parent.parent)

    out = subprocess.run([sys.executable, "-c", _SIGTERM_PROBE], env=env,
                         capture_output=True, text=True, timeout=120).stdout
    assert out.strip() == "is_default=True", (
        f"import must not install a SIGTERM handler, got {out!r}"
    )


def test_auto_scrape_loop_yields_when_lock_held_elsewhere(tmp_path, monkeypatch):
    """A second process must NOT start an auto-scrape loop.

    The systemd unit runs `gunicorn --workers 2` without `--preload`, so every
    worker imports the app and reaches `_ensure_auto_loop()` independently.
    `_scrape_lock`/`_scraping` are per-process, so without a cross-process
    election both workers would start a loop, both would see stale data, and
    both would scrape the same SQLite file concurrently — duplicating work and
    email alerts and contending on the write lock.

    Here another holder takes the advisory lock first; `_ensure_auto_loop()`
    must then decline to start. flock conflicts between distinct open file
    descriptions even within one process, so holding it here faithfully
    models the second worker.
    """
    import fcntl

    lock_path = tmp_path / "data.db.auto-scrape.lock"
    holder = open(lock_path, "w")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)

    monkeypatch.setattr(A.config, "DB_PATH", tmp_path / "data.db")
    monkeypatch.setattr(A, "_auto_loop_started", False)
    threads_before = threading.active_count()
    # This process already won the election for the *real* DB at import time,
    # so the global is non-None. What matters is that it is not replaced by a
    # handle on the tmp lockfile we are holding.
    lock_fh_before = A._auto_loop_lock_fh

    A._ensure_auto_loop()

    assert A._auto_loop_started is False, "must not start a second loop"
    assert A._auto_loop_lock_fh is lock_fh_before, "must not have taken the lock"
    assert threading.active_count() == threads_before, "must not spawn a thread"
