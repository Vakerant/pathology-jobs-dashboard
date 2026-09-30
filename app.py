"""Flask dashboard server for the pathology jobs/exam tracker — industry-grade slice 1.

- Gunicorn-ready (no dev server in prod), health/ready probes, env validation.
- Hardened /api/flag (allowlist, type checks, 40-char key validation).
- TLS verification is fixed in scraper.py; this service just serves.
"""
import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone, timedelta

from flask import Flask, jsonify, render_template, request

import db
import scraper
import alerts
import export_static
import config

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

# _scrape_lock guards ONLY the atomic claim of the scrape slot. The scrape
# itself must NOT hold it, or a slow scrape would block every other caller
# for its whole duration instead of being idempotently rejected.
_scrape_lock = threading.Lock()
_scraping = {"running": False}
_auto_loop_started = False
# Held open for the process lifetime by whichever process wins the auto-scrape
# lock; see _ensure_auto_loop.
_auto_loop_lock_fh = None

# Auto-update: while the dashboard runs, re-scrape whenever data gets older
# than this (covers laptops that are asleep when the daily timer fires).
# Single source of truth: config.py (env-overridable, see .env.example).
AUTO_SCRAPE_HOURS = config.AUTO_SCRAPE_HOURS
# How often the auto-scrape loop wakes up to re-check staleness.
AUTO_SCRAPE_POLL_SECONDS = 900
# Set on shutdown so the auto loop can exit promptly instead of sleeping out
# its full poll interval. A throwaway Event per iteration can never be
# signalled, which previously made graceful shutdown impossible.
_shutdown = threading.Event()

# Flag allowlist — never interpolate user input into SQL without this.
ALLOWED_FLAG_FIELDS = {"starred", "hidden"}
_KEY_RE = config.FLAG_KEY_RE


# ---------------------------------------------------------------------------
# Abuse protection for the mutating endpoints
# ---------------------------------------------------------------------------
# The dashboard's own JS is the only client for both /api/refresh and /api/flag
# (templates/dashboard.html calls them around L951 and L980), so neither can be
# gated behind a secret: anything the browser can send is public by definition,
# and requiring a token would simply break the shipped dashboard.
#
# Rate limiting is therefore the control that actually matters. Without it, one
# POST /api/refresh fans out to len(SOURCES) concurrent scrapes against
# government servers, so any reachable caller becomes an amplifier. This is what
# makes config.ADMIN_RATE_LIMIT a real control rather than dead config.
_RATE_LIMIT_LOCK = threading.Lock()
# client ip -> deque of monotonic timestamps inside the current window
_RATE_BUCKETS: dict[str, deque] = {}
# Hard cap on tracked IPs: a rotating-source-address flood must not be able to
# grow this dict without bound. Buckets whose window has fully expired go first.
_RATE_MAX_BUCKETS = 4096
_RATE_WINDOW_SECONDS = 60.0

# Path -> allowed requests per window. refresh is orders of magnitude more
# expensive than flag (one DB row write vs a whole multi-source network scrape),
# so it gets a much tighter allowance.
_RATE_LIMITED_PATHS = {
    "/api/refresh": config.REFRESH_RATE_LIMIT,
    "/api/flag": config.ADMIN_RATE_LIMIT,
}


def _rate_limit_reset():
    """Forget every recorded request timestamp. Test hook."""
    with _RATE_LIMIT_LOCK:
        _RATE_BUCKETS.clear()


def _consume_rate_limit(limit):
    """Sliding-window per-IP limiter.

    Returns None when the request may proceed, or (retry_after_seconds, limit)
    when it must be rejected with HTTP 429.
    """
    ident = request.remote_addr or "unknown"
    now = time.monotonic()
    with _RATE_LIMIT_LOCK:
        bucket = _RATE_BUCKETS.setdefault(ident, deque())
        # Drop timestamps that have slid out of the window.
        while bucket and now - bucket[0] >= _RATE_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= limit:
            retry_after = max(1, int(_RATE_WINDOW_SECONDS - (now - bucket[0])))
            return retry_after, limit
        bucket.append(now)
        # Opportunistic prune, then cap. Keeps memory bounded even if the
        # limiter is being attacked from rotating addresses.
        #
        # This MUST sweep every bucket by its own oldest timestamp, not just
        # delete empty ones: a bucket is only emptied by the sliding-window trim
        # above, which runs for the REQUESTING ip alone. Deleting only empty
        # buckets can therefore never free a quiet client, and every such ip
        # would sit resident until the hard cap evicted it.
        if len(_RATE_BUCKETS) > 1:
            stale = [ip for ip, ts in _RATE_BUCKETS.items()
                     if now - ts[0] >= _RATE_WINDOW_SECONDS]
            for ip in stale:
                if ip != ident:
                    del _RATE_BUCKETS[ip]
        if len(_RATE_BUCKETS) > _RATE_MAX_BUCKETS:
            for ip in list(_RATE_BUCKETS)[: len(_RATE_BUCKETS) - _RATE_MAX_BUCKETS]:
                del _RATE_BUCKETS[ip]
    return None


@app.before_request
def _enforce_rate_limit():
    """Throttle the mutating endpoints only.

    GET / and GET /api/data are deliberately NOT limited: the dashboard polls
    them, and a 429 there would break the product for no security benefit.
    """
    limit = _RATE_LIMITED_PATHS.get(request.path)
    if not limit or limit <= 0:
        return None
    denial = _consume_rate_limit(limit)
    if denial is None:
        return None
    retry_after, cap = denial
    log.warning(
        "rate limit exceeded: %s on %s (limit %d per %ds)",
        request.remote_addr, request.path, cap, int(_RATE_WINDOW_SECONDS),
    )
    resp = jsonify({
        "ok": False,
        "error": "rate limit exceeded",
        "limit": cap,
        "window_seconds": int(_RATE_WINDOW_SECONDS),
    })
    resp.status_code = 429
    resp.headers["Retry-After"] = str(retry_after)
    return resp


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
        "scraping": _is_scraping(),
        # What the most recent scrape actually did. Without these the UI can only
        # say "updating..." and then go quiet, which is indistinguishable from a
        # silent failure — see the toast in templates/dashboard.html::refresh().
        "last_run_new": _meta_int("last_run_new"),
        "last_run_ok": _meta_int("last_run_ok"),
        "last_run_failed": _meta_int("last_run_failed"),
        "projection_ok": db.get_meta("last_run_projection_ok") == "1",
        "orphans": _meta_int("last_run_orphans"),
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
        # Release the slot under the lock so a claim can never interleave
        # with the clear and observe a stale True.
        with _scrape_lock:
            _scraping["running"] = False


def _is_scraping() -> bool:
    with _scrape_lock:
        return bool(_scraping["running"])


def _meta_int(key: str) -> int:
    """Read an integer out of the meta table, tolerating missing/garbage values.

    meta rows are written as TEXT by db.set_meta and may be absent entirely on a
    fresh database, so every caller here wants a number, never an exception.
    """
    try:
        return int(db.get_meta(key) or 0)
    except (TypeError, ValueError):
        return 0


def _start_scrape() -> bool:
    """Kick off a scrape unless one is already running. Returns True if started.

    The claim is a check-and-set performed *while holding* ``_scrape_lock``,
    which is what makes concurrent /api/refresh calls safe. Previously the
    lock was released before the spawned thread did any work, so the real
    guard was a lock-free check-then-set on the ``_scraping`` dict and two
    callers could both pass it and launch overlapping scrapes.
    """
    with _scrape_lock:
        if _scraping["running"]:
            log.info("scrape already running — skip")
            return False
        _scraping["running"] = True
    # Start outside the lock: the scrape outlives the claim by design.
    log.info("starting scrape")
    threading.Thread(target=_do_scrape, name="scrape", daemon=True).start()
    return True


def _auto_scrape_loop():
    """Every AUTO_SCRAPE_POLL_SECONDS: scrape if the data is stale.

    Runs as a daemon thread and wakes on ``_shutdown`` so it can exit
    promptly on SIGTERM instead of sleeping out a full poll interval.
    """
    while not _shutdown.is_set():
        try:
            last = db.get_meta("last_run")
            stale = (not last or
                     datetime.fromisoformat(last) <
                     datetime.now(timezone.utc) - timedelta(hours=AUTO_SCRAPE_HOURS))
            if stale:
                _start_scrape()
        except Exception as exc:  # noqa: BLE001
            log.warning("auto_scrape_loop error: %s", exc)
        _shutdown.wait(AUTO_SCRAPE_POLL_SECONDS)
    log.info("auto-scrape loop stopped")


def _ensure_auto_loop():
    """Start the background auto-scrape loop, at most once per *host*.

    ``_scrape_lock`` and ``_scraping`` are per-process, so an in-process guard
    is not enough: the systemd unit runs ``gunicorn --workers 2`` without
    ``--preload``, which means every worker imports this module independently.
    Each would start its own loop, and once the data went stale both would
    fire a scrape against the same SQLite file — duplicated work, duplicated
    email alerts, and write contention.

    An exclusive advisory lock on a lockfile elects a single owner. The first
    worker to claim it runs the loop and keeps the fd open for the life of the
    process; the losers skip and leave the loop to the winner.
    """
    global _auto_loop_started, _auto_loop_lock_fh
    if _auto_loop_started:
        return
    try:
        import fcntl
        fh = open(str(config.DB_PATH) + ".auto-scrape.lock", "w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            log.info("auto-scrape loop owned by another process — skipping")
            return
        # Keep the handle referenced: closing it would release the lock.
        _auto_loop_lock_fh = fh
    except Exception as exc:  # noqa: BLE001
        # Locking unavailable (exotic filesystem, no fcntl) — degrade to the
        # per-process guard: one loop per worker beats no loop at all.
        log.warning("auto-scrape lock unavailable (%s) — per-process guard only",
                    exc)

    _auto_loop_started = True
    threading.Thread(target=_auto_scrape_loop, name="auto-scrape",
                     daemon=True).start()
    log.info("auto-scrape loop started (every %ss, stale=%sh)",
             AUTO_SCRAPE_POLL_SECONDS, AUTO_SCRAPE_HOURS)


def _install_signal_handlers():
    """Flip _shutdown on SIGTERM/SIGINT so the auto loop drains cleanly.

    Only ever called from the ``__main__`` path. Under a WSGI server this must
    NOT run: gunicorn imports the app in each worker's main thread, so
    ``signal.signal`` would *succeed* and silently replace gunicorn's own
    SIGTERM/SIGINT handlers with a handler that only sets a flag. The worker
    would then ignore the signal that gunicorn relies on for graceful exit and
    hang until SIGKILL, breaking ``systemctl restart``.
    """
    import signal

    def _handle(signum, _frame):
        log.info("received signal %s — shutting down", signum)
        _shutdown.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handle)
        except (ValueError, OSError):
            # Not on the main thread — the daemon thread is torn down at exit.
            log.debug("could not install handler for %s", sig)


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    started = _start_scrape()
    # 202 if we started, 200 if already running — both succeed but caller knows
    return jsonify({"started": started, "running": _is_scraping()}), (202 if started else 200)


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
if config.AUTO_SCRAPE_ENABLED:
    _ensure_auto_loop()
else:
    # Importing this module must never start outbound network traffic on its
    # own: tests import `app`, and an unguarded loop turned a `pytest` run into
    # a live scrape of every configured source.
    log.info("auto-scrape loop disabled (AUTO_SCRAPE_ENABLED=0)")

if __name__ == "__main__":
    # Direct run (systemd ExecStart with `python app.py` still works)
    # Host/port are intentionally localhost; expose via reverse proxy in prod.
    # Signal handlers are installed here rather than at import: under gunicorn
    # this block never runs, so we never clobber gunicorn's own handlers.
    _install_signal_handlers()
    app.run(host="127.0.0.1", port=5000, debug=False)
