"""Pytest configuration: ensure project root is importable."""
import atexit
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# app.py starts a background auto-scrape loop at import time, which scrapes
# every configured government site over the network. Several test modules
# import `app`, so a bare `pytest` run would otherwise fire real outbound
# requests and write to a real database — the suite would no longer be
# hermetic, and the CI test job would become network- and timing-dependent.
# This MUST be set before any test module imports app (conftest is collected
# first). Set explicitly rather than with setdefault so a developer's exported
# AUTO_SCRAPE_ENABLED=1 cannot turn it back on for the test run.
os.environ["AUTO_SCRAPE_ENABLED"] = "0"

# app.py also runs init_db() at import time, during collection and so before any
# fixture. Point it at a scratch directory rather than the real data.db.
_SCRATCH = tempfile.mkdtemp(prefix="pathology-tests-")
atexit.register(shutil.rmtree, _SCRATCH, ignore_errors=True)
os.environ["DB_PATH"] = os.path.join(_SCRATCH, "import-time.db")
os.environ["BACKUP_DIR"] = os.path.join(_SCRATCH, "backups")

import pytest


@pytest.fixture(autouse=True)
def _isolate_from_real_data(tmp_path, monkeypatch):
    """Keep every test away from the developer's real data.db and backups/.

    Most tests repoint only db.DB_PATH, but migrations.backup() and the scrape
    lockfile read config.DB_PATH. Each run therefore checkpointed the real
    database and copied it into the real backups/ directory, where the copies
    also pushed older real backups out of retention (BACKUP_KEEP), and a few
    tests silently read live data. Tests that need a specific path still
    override these. Each test starts from an initialised, empty scratch DB.
    """
    import config
    import db
    placeholder = tmp_path / "_isolated.db"
    monkeypatch.setattr(config, "DB_PATH", placeholder)
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "_backups")
    monkeypatch.setattr(db, "DB_PATH", str(placeholder))
    monkeypatch.setattr(db, "BACKUP_DIR", tmp_path / "_backups")
    db.init_db()


@pytest.fixture(autouse=True)
def _isolate_rate_limit_state():
    """Reset the per-IP rate-limit buckets around every test.

    The limiter in app.py keeps module-global state (a dict of client IP ->
    request timestamps) which is exactly right in production and wrong in a
    suite: without this, tests that legitimately POST /api/refresh more than
    REFRESH_RATE_LIMIT times -- e.g. tests/test_app_scrape_lock.py, which pokes
    the endpoint repeatedly to prove the scrape slot is claimed only once --
    would start receiving 429s and fail for a reason unrelated to what they
    assert. Importing app is safe here because AUTO_SCRAPE_ENABLED=0 is set
    above, before any test module can import it.
    """
    try:
        import app as _app
    except Exception:  # noqa: BLE001 - a collection error should not mask these
        yield
        return
    _app._rate_limit_reset()
    yield
    _app._rate_limit_reset()
