"""
Central, environment-driven configuration for the Pathology Career Intelligence
Platform. No secrets, personal identifiers, or credentials live in code — every
sensitive value is read from the environment (or a local .env file, which is
gitignored). See `.env.example` for the full list.

Design rules:
- Config over constants: every tunable is overridable via env.
- Fail-fast on obviously-broken values (e.g. a non-integer port).
- Never default a secret to a real value. Missing secrets mean the feature
  is disabled (alerts skip, admin route 404s), never a silent insecure default.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# Load a local .env if python-dotenv is available. Optional — everything also
# works via real environment variables (systemd, cron, CI).
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except Exception:  # noqa: BLE001 — dotenv is optional
    pass

HERE = Path(__file__).resolve().parent


def _env(key: str, default: str | None = None) -> str | None:
    val = os.environ.get(key)
    return val if val not in (None, "") else default


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise RuntimeError(f"{key} must be an integer, got {raw!r}") from None


def _env_bool(key: str, default: bool) -> bool:
    raw = _env(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_list(key: str, default: list[str] | None = None) -> list[str]:
    raw = _env(key)
    if raw is None:
        return list(default or [])
    return [p.strip() for p in raw.split(",") if p.strip()]


# ---------------------------------------------------------------------------
# Paths & database
# ---------------------------------------------------------------------------
DB_PATH = Path(_env("DB_PATH") or str(HERE / "data.db"))
BACKUP_DIR = Path(_env("BACKUP_DIR") or str(HERE / "backups"))

# ---------------------------------------------------------------------------
# Timezone — all storage is UTC; all *display* defaults to Asia/Kolkata.
# ---------------------------------------------------------------------------
TZ = "Asia/Kolkata"

# ---------------------------------------------------------------------------
# Scraper / crawler
# ---------------------------------------------------------------------------
CRAWLER_USER_AGENT = _env(
    "CRAWLER_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
)
SCRAPE_TIMEOUT = (_env_int("SCRAPE_CONNECT_TIMEOUT", 10), _env_int("SCRAPE_READ_TIMEOUT", 30))
MAX_WORKERS = _env_int("MAX_WORKERS", 10)
MAX_ITEMS_PER_SOURCE = _env_int("MAX_ITEMS_PER_SOURCE", 60)
AUTO_SCRAPE_HOURS = _env_int("AUTO_SCRAPE_HOURS", 6)
# Hosts that genuinely need TLS verify disabled (broken cert chains).
#
# Several govt/INI sites serve an INCOMPLETE TLS cert chain (they omit their
# intermediate CA), so the standard cert bundle cannot verify them even though
# the site is legitimate. Default these to verify=False so the daily scrape
# works out of the box; operators can still append overrides via
# PATHO_INSECURE_HOSTS without losing these defaults.
_DEFAULT_INSECURE_HOSTS = [
    "pgimer.edu.in", "jipmer.edu.in", "nimhans.ac.in",
    "uhsr.ac.in", "aimsschamiana.edu.in",
]
INSECURE_HOSTS = set(_env_list("PATHO_INSECURE_HOSTS", _DEFAULT_INSECURE_HOSTS))

# ---------------------------------------------------------------------------
# Retention / pruning — a source returning 0 results must NOT delete prior jobs;
# we only prune on *reliable absence* (unseen for N days) and never prune seeds.
# ---------------------------------------------------------------------------
PRUNE_UNSEEN_DAYS = _env_int("PRUNE_UNSEEN_DAYS", 45)
STALE_NOTICE_DAYS = _env_int("STALE_NOTICE_DAYS", 45)

# ---------------------------------------------------------------------------
# Email alerts — disabled unless credentials are present.
# ---------------------------------------------------------------------------
SMTP_HOST = _env("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = _env_int("SMTP_PORT", 465)
SMTP_SENDER = _env("SMTP_SENDER")            # required for alerts to run
SMTP_APP_PASSWORD = _env("SMTP_APP_PASSWORD")  # required for alerts to run
ALERT_RECIPIENTS = _env_list("ALERT_RECIPIENTS")  # comma-separated


def email_config() -> dict | None:
    """Return a ready-to-use SMTP config, or None if alerts are not configured."""
    if not SMTP_SENDER or not SMTP_APP_PASSWORD:
        return None
    recipients = ALERT_RECIPIENTS or [SMTP_SENDER]
    return {
        "smtp_host": SMTP_HOST,
        "smtp_port": SMTP_PORT,
        "sender": SMTP_SENDER,
        "app_password": SMTP_APP_PASSWORD,
        "recipients": recipients,
    }


# ---------------------------------------------------------------------------
# Admin / security
# ---------------------------------------------------------------------------
ADMIN_SECRET = _env("ADMIN_SECRET")           # unset => admin route is disabled
ADMIN_RATE_LIMIT = _env_int("ADMIN_RATE_LIMIT", 30)   # req/min
FLAG_KEY_RE = re.compile(r"^[0-9a-f]{40}$")   # listing keys are 40-char hex

# ---------------------------------------------------------------------------
# LLM extraction (DeepSeek by default, provider-agnostic) — disabled unless key.
# ---------------------------------------------------------------------------
LLM_PROVIDER = _env("LLM_PROVIDER", "deepseek")
LLM_API_KEY = _env("LLM_API_KEY")
LLM_MODEL = _env("LLM_MODEL", "deepseek-chat")
LLM_BASE_URL = _env("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_SCHEMA_VERSION = "1"
LLM_ENABLED = _env_bool("LLM_ENABLED", False) and LLM_API_KEY is not None

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
FLASK_HOST = _env("FLASK_HOST", "127.0.0.1")
FLASK_PORT = _env_int("FLASK_PORT", 5000)
FLASK_DEBUG = _env_bool("FLASK_DEBUG", False)
