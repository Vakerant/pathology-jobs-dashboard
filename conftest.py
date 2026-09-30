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
