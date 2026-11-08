"""
Email alerts for NEW pathology posts.

Runs after each scrape. Finds high-relevance (clearly-pathology) listings that
haven't been emailed yet, sends a single grouped HTML digest, then marks them
sent so you never get a duplicate.

Credentials come from environment variables (see `.env.example`) or, as a local
fallback, `email_config.json` (NOT in code, NOT in git). Template:

    {
      "smtp_host": "smtp.gmail.com",
      "smtp_port": 465,
      "sender":    "you@example.com",
      "app_password": "xxxx xxxx xxxx xxxx",   <-- Gmail App Password (16 chars)
      "recipients": ["you@example.com"]
    }

Get a Gmail App Password:  Google Account -> Security -> 2-Step Verification (on)
-> App passwords -> create one for "Mail". Paste the 16-char code above.

Usage:
    python alerts.py            # send digest for any new pathology posts
    python alerts.py --baseline # mark all current listings as already-sent (run once)
    python alerts.py --test     # send a test email to confirm credentials work
"""
import html as html_lib
import json
import os
import smtplib
import ssl
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urlsplit

import db
import config

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "email_config.json")

# Hard ceiling on the whole SMTP conversation. Without this, a black-holed
# connection (firewalled port, server that accepts TCP then stalls, a hung
# auth step) parks the calling thread indefinitely. That thread is the scrape
# thread in both entry points — app._do_scrape and run_daily.sh — so a stalled
# SMTP server would wedge the daily pipeline and, in the app, hold the
# cross-process scrape claim forever so no worker could ever scrape again.
SMTP_TIMEOUT = 30

_REQUIRED_KEYS = ("smtp_host", "smtp_port", "sender", "app_password", "recipients")


def _validate_config(cfg):
    """Return a normalised config dict, or None if it is unusable.

    Validating here turns what used to be a late ``KeyError`` inside
    ``_send`` — swallowed by ``send_new``'s blanket except and reported only
    as a generic "send failed" — into an explicit, actionable message at the
    point the config is loaded.
    """
    if not isinstance(cfg, dict):
        return None, "config is not a JSON object"
    missing = [k for k in _REQUIRED_KEYS if k not in cfg]
    if missing:
        return None, f"missing key(s): {', '.join(missing)}"
    recipients = cfg["recipients"]
    if not isinstance(recipients, list) or not recipients or \
            not all(isinstance(r, str) and "@" in r for r in recipients):
        return None, "'recipients' must be a non-empty list of email addresses"
    try:
        port = int(cfg["smtp_port"])
    except (TypeError, ValueError):
        return None, "'smtp_port' must be an integer"
    if not (0 < port < 65536):
        return None, f"'smtp_port' out of range: {port}"
    return {
        "smtp_host": str(cfg["smtp_host"]),
        "smtp_port": port,
        "sender": str(cfg["sender"]),
        "app_password": str(cfg["app_password"]),
        "recipients": recipients,
    }, None


def load_config(verbose=False):
    # Prefer env-driven config (SMTP_SENDER / SMTP_APP_PASSWORD / ALERT_RECIPIENTS),
    # then fall back to the local email_config.json file. Returns None if neither
    # is configured so alerts are simply skipped.
    env_cfg = config.email_config()
    if env_cfg is not None:
        cfg, err = _validate_config(env_cfg)
        if cfg is None and verbose:
            print(f"Alert config from env is invalid ({err}) — skipping alerts.")
        return cfg
    if not os.path.exists(CONFIG_PATH):
        return None
    try:
        with open(CONFIG_PATH) as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        # A malformed file used to raise straight out of load_config() and take
        # the entire scrape pipeline's alert stage down with a traceback.
        if verbose:
            print(f"{CONFIG_PATH} is unreadable ({e}) — skipping alerts.")
        return None
    cfg, err = _validate_config(raw)
    if cfg is None and verbose:
        print(f"{CONFIG_PATH} is invalid ({err}) — skipping alerts.")
    return cfg


def _send(cfg, subject, html_body):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Pathology Tracker <{cfg['sender']}>"
    msg["To"] = ", ".join(cfg["recipients"])
    msg.attach(MIMEText("Open in an HTML-capable mail client.", "plain"))
    msg.attach(MIMEText(html_body, "html"))
    ctx = ssl.create_default_context()
    # timeout= covers connect, the TLS handshake, login and each send.
    with smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"],
                          context=ctx, timeout=SMTP_TIMEOUT) as s:
        s.login(cfg["sender"], cfg["app_password"].replace(" ", ""))
        s.sendmail(cfg["sender"], cfg["recipients"], msg.as_string())


def _esc(value):
    """HTML-escape a scraped string for safe interpolation into the digest.

    Every field below originates on a third-party government web page that the
    scraper does not control. Interpolated raw, a title containing ``<script>``
    or a URL containing a quote injects arbitrary markup into the alert email
    (and a stray ``"`` breaks out of the href attribute). The dashboard has a JS
    ``esc()`` helper for the same reason; this is its Python counterpart.
    """
    return html_lib.escape(str(value if value is not None else ""), quote=True)


def _link(url, title):
    """Anchor for http(s) URLs; plain escaped text for any other scheme.

    ``html.escape`` stops attribute breakout but not a ``javascript:`` or
    ``data:`` URL, and the URL comes from a third-party page.
    """
    style = "font-size:15px;font-weight:600;color:#1a56db;text-decoration:none"
    if urlsplit(str(url or "").strip()).scheme.lower() in ("http", "https"):
        return (f'<a href="{_esc(url)}" rel="noopener noreferrer" '
                f'style="{style}">{_esc(title)}</a>')
    return f'<span style="{style}">{_esc(title)}</span>'


def _digest_html(items):
    by_region = {}
    for it in items:
        by_region.setdefault(it["region"], []).append(it)
    blocks = []
    for region in sorted(by_region):
        rows = ""
        for it in by_region[region]:
            snip = f"<div style='color:#555;font-size:13px;margin-top:3px'>{_esc(it.get('snippet'))}</div>" if it.get("snippet") else ""
            rows += f"""
            <div style="padding:12px 0;border-bottom:1px solid #eee">
              {_link(it['url'], it['title'])}
              <div style="color:#777;font-size:12px;margin-top:3px">{_esc(it.get('source_name'))} · {_esc(it.get('category'))}</div>
              {snip}
            </div>"""
        blocks.append(f"""
          <h3 style="margin:22px 0 4px;color:#111;font-size:14px;text-transform:uppercase;letter-spacing:.5px">
            {_esc(region)} <span style="color:#999;font-weight:400">({len(by_region[region])})</span></h3>
          {rows}""")
    return f"""
    <div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:640px;margin:auto;color:#222">
      <h2 style="color:#0b7;margin-bottom:2px">🔬 {len(items)} new pathology post{'s' if len(items)!=1 else ''}</h2>
      <div style="color:#888;font-size:12px;margin-bottom:10px">{datetime.now().strftime('%d %b %Y, %I:%M %p')} · from your SR-exam & jobs tracker</div>
      {''.join(blocks)}
      <div style="margin-top:26px;color:#999;font-size:11px">
        Always confirm dates &amp; eligibility on the official source page before applying.<br>
        Dashboard: <a href="http://localhost:5000">http://localhost:5000</a>
      </div>
    </div>"""


def send_new(verbose=True):
    cfg = load_config(verbose=verbose)
    if not cfg:
        if verbose:
            print("No email_config.json (or env vars) found — skipping alerts. "
                  "Copy email_config.example.json to email_config.json and add your Gmail App Password.")
        return 0
    items = db.fetch_unalerted_pathology()
    if not items:
        if verbose:
            print("No new pathology posts to alert.")
        return 0
    subject = f"🔬 {len(items)} new pathology post{'s' if len(items)!=1 else ''} — SR/jobs/fellowships"
    try:
        _send(cfg, subject, _digest_html(items))
        db.mark_alerted([it["key"] for it in items])
        if verbose:
            print(f"Sent alert for {len(items)} new pathology post(s) to {', '.join(cfg['recipients'])}.")
        return len(items)
    except Exception as e:  # noqa: BLE001
        if verbose:
            print(f"Alert send FAILED: {e}")
        return -1


def send_test():
    cfg = load_config(verbose=True)
    if not cfg:
        print("No email_config.json found. Create it first (see email_config.example.json).")
        return
    html = _digest_html([{
        "region": "Test", "title": "✅ Test alert — your pathology tracker email works",
        "url": "http://localhost:5000", "source_name": "Pathology Tracker",
        "category": "Test", "snippet": "If you can read this, alerts are configured correctly."}])
    try:
        _send(cfg, "🔬 Test alert — Pathology Tracker", html)
        print(f"Test email sent to {', '.join(cfg['recipients'])}. Check your inbox (and Spam).")
    except Exception as e:  # noqa: BLE001
        print(f"FAILED: {e}\nCheck the App Password (16 chars, no spaces) and that 2-Step Verification is ON.")


if __name__ == "__main__":
    config.setup_logging()
    db.init_db()
    if "--baseline" in sys.argv:
        n = db.baseline_mark_all_alerted()
        print(f"Baseline set: {n} existing listings marked as already-alerted. "
              "Future scrapes will only email genuinely new pathology posts.")
    elif "--test" in sys.argv:
        send_test()
    else:
        send_new()
