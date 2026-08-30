"""Flask dashboard server for the pathology jobs/exam tracker."""
import threading
from datetime import datetime, timezone, timedelta

from flask import Flask, jsonify, render_template, request

import db
import scraper
import alerts
import export_static

app = Flask(__name__)
_scrape_lock = threading.Lock()
_scraping = {"running": False}

# Auto-update: while the dashboard runs, re-scrape whenever data gets older
# than this (covers laptops that are asleep when the daily timer fires).
AUTO_SCRAPE_HOURS = 6


@app.route("/")
def index():
    return render_template("dashboard.html")


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


def _do_scrape():
    try:
        scraper.run(verbose=False)
        try:
            alerts.send_new(verbose=False)      # email new pathology posts
        except Exception:
            pass
        try:
            export_static.build()               # refresh static mirror data
        except Exception:
            pass
    finally:
        _scraping["running"] = False


def _start_scrape():
    """Kick off a scrape unless one is already running. Returns True if started."""
    if _scrape_lock.acquire(blocking=False):
        try:
            if not _scraping["running"]:
                _scraping["running"] = True
                threading.Thread(target=_do_scrape, daemon=True).start()
                return True
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
        except Exception:
            pass
        threading.Event().wait(900)


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    _start_scrape()
    return jsonify({"started": True})


@app.route("/api/flag", methods=["POST"])
def api_flag():
    data = request.get_json(force=True)
    key, field, value = data["key"], data["field"], data["value"]
    if field in ("starred", "hidden"):
        db.set_flag(key, field, value)
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 400


if __name__ == "__main__":
    db.init_db()
    threading.Thread(target=_auto_scrape_loop, daemon=True).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
