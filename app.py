"""Flask dashboard server for the pathology jobs/exam tracker — industry-grade slice 1.

- Gunicorn-ready (no dev server in prod), health/ready probes, env validation.
- Hardened /api/flag (allowlist, type checks, 40-char key validation).
- TLS verification is fixed in scraper.py; this service just serves.
"""
import logging
import os
import re
import threading
from datetime import datetime, timezone, timedelta

from flask import Flask, jsonify, render_template, request

import db
import scraper
import alerts
import export_static

# ---------------------------------------------------------------------------
# App & infra
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("pathology-dashboard")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024  # flag payload is tiny

_scrape_lock = threading.Lock()
_scraping = {"running": False}
_auto_loop_started = False

# Auto-update: while the dashboard runs, re-scrape whenever data gets older
# than this (covers laptops that are asleep when the daily timer fires).
AUTO_SCRAPE_HOURS = 6

# Flag allowlist — never interpolate user input into SQL without this.
ALLOWED_FLAG_FIELDS = {"starred", "hidden"}
_KEY_RE = re.compile(r"^[0-9a-f]{40}$")


def _validate_env():
    """Fail fast if DB directory is not writable — industry-grade startup check."""
    try:
        db.init_db()
        # prove we can write
        db.set_meta("_healthcheck", datetime.now(timezone.utc).isoformat())
    except Exception as exc:
        log.error("DB init failed at %s: %s", db.DB_PATH, exc)
        raise RuntimeError(f"DB init failed at {db.DB_PATH}: {exc}") from exc
    log.info("DB ready at %s", db.DB_PATH)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("dashboard.html")


@app.get("/healthz")
def healthz():
    """Liveness: process is up. No dependencies checked."""
    return jsonify({"status": "ok"}), 200


@app.get("/readyz")
def readyz():
    """Readiness: DB reachable + last_run present. Used by systemd/K8s probes."""
    try:
        conn = db.get_conn()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        last = db.get_meta("last_run")
        return jsonify({"status": "ready", "last_run": last}), 200
    except Exception as exc:  # noqa: BLE001
        log.warning("readyz failed: %s", exc)
        return jsonify({"status": "unready", "error": str(exc)[:200]}), 503


@app.route("/api/data")
def api_data():
    listings = db.fetch_listings()
    status = db.fetch_status()
    db.enrich(listings)
    visible = [l for l in listings if not l["hidden"]]
    active_path = [l for l in visible if l["relevance"] == "high" and not l["expired"]]
    stats = {
        "total": len(visible),
        "new_week": sum(1 for l in visible if l["is_new"]),
        "starred": sum(1 for l in visible if l["starred"]),
        "high": sum(1 for l in visible if l["relevance"] == "high"),
        "active_pathology": len(active_path),
        "closing_soon": sum(1 for l in visible if l.get("closing_soon")),
        "expired": sum(1 for l in visible if l["expired"]),
        "sources_ok": sum(1 for s in status if s["status"] == "ok"),
        "sources_failed": sum(1 for s in status if s["status"] == "failed"),
        "last_run": db.get_meta("last_run"),
        "scraping": _scraping["running"],
    }
    return jsonify({"listings": listings, "status": status, "stats": stats})


# ---------------------------------------------------------------------------
# Scrape orchestration
# ---------------------------------------------------------------------------
def _do_scrape():
    try:
        scraper.run(verbose=False)
        try:
            alerts.send_new(verbose=False)      # email new pathology posts
        except Exception as exc:  # noqa: BLE001
            log.warning("alerts.send_new failed: %s", exc)
        try:
            export_static.build()               # refresh static mirror data
        except Exception as exc:  # noqa: BLE001
            log.warning("export_static.build failed: %s", exc)
    except Exception as exc:  # noqa: BLE001
        log.exception("scrape run failed: %s", exc)
    finally:
        _scraping["running"] = False


def _start_scrape():
    """Kick off a scrape unless one is already running. Returns True if started."""
    if _scrape_lock.acquire(blocking=False):
        try:
            if not _scraping["running"]:
                _scraping["running"] = True
                log.info("starting scrape")
                threading.Thread(target=_do_scrape, daemon=True).start()
                return True
            log.info("scrape already running — skip")
            return False
        finally:
            _scrape_lock.release()
    return False


def _auto_scrape_loop():
    """Every 15 min: scrape if the data is stale. Runs as a daemon thread."""
    while True:
        try:
            last = db.get_meta("last_run")
            stale = (not last or
                     datetime.fromisoformat(last) <
                     datetime.now(timezone.utc) - timedelta(hours=AUTO_SCRAPE_HOURS))
            if stale:
                _start_scrape()
        except Exception as exc:  # noqa: BLE001
            log.warning("auto_scrape_loop error: %s", exc)
        threading.Event().wait(900)


def _ensure_auto_loop():
    global _auto_loop_started
    if _auto_loop_started:
        return
    _auto_loop_started = True
    threading.Thread(target=_auto_scrape_loop, daemon=True).start()
    log.info("auto-scrape loop started (every 15 min, stale=%sh)", AUTO_SCRAPE_HOURS)


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    started = _start_scrape()
    # 202 if we started, 200 if already running — both succeed but caller knows
    return jsonify({"started": started, "running": _scraping["running"]}), (202 if started else 200)


@app.route("/api/flag", methods=["POST"])
def api_flag():
    """Hardened: allowlist field, validate key (40-char hex), value 0/1."""
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify({"ok": False, "error": "invalid JSON"}), 400

    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid payload"}), 400

    key = data.get("key")
    field = data.get("field")
    value = data.get("value")

    if field not in ALLOWED_FLAG_FIELDS:
        return jsonify({"ok": False, "error": f"field must be one of {sorted(ALLOWED_FLAG_FIELDS)}"}), 400
    if not isinstance(key, str) or not _KEY_RE.match(key):
        return jsonify({"ok": False, "error": "invalid key (expected 40-char hex)"}), 400
    if value not in (0, 1, True, False):
        return jsonify({"ok": False, "error": "value must be 0 or 1"}), 400

    try:
        updated = db.set_flag(key, field, int(value))
        if updated == 0:
            return jsonify({"ok": False, "error": "key not found"}), 404
        return jsonify({"ok": True}), 200
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        log.exception("set_flag failed: %s", exc)
        return jsonify({"ok": False, "error": "internal error"}), 500


# ---------------------------------------------------------------------------
# Startup — works for both `python app.py` and `gunicorn app:app`
# ---------------------------------------------------------------------------
_validate_env()
_ensure_auto_loop()

if __name__ == "__main__":
    # Direct run (systemd ExecStart with `python app.py` still works)
    # Host/port are intentionally localhost; expose via reverse proxy in prod.
    app.run(host="127.0.0.1", port=5000, debug=False)
