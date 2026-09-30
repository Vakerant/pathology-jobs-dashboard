"""Pytest configuration: ensure project root is importable."""
import os
import sys

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

import pytest


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
