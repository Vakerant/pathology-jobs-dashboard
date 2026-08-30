"""
Email alerts for NEW pathology posts.

Runs after each scrape. Finds high-relevance (clearly-pathology) listings that
haven't been emailed yet, sends a single grouped HTML digest, then marks them
sent so you never get a duplicate.

Credentials live in `email_config.json` (NOT in code, NOT in git). Template:

    {
      "smtp_host": "smtp.gmail.com",
      "smtp_port": 465,
      "sender":    "vbhvverma7@gmail.com",
      "app_password": "xxxx xxxx xxxx xxxx",   <-- Gmail App Password (16 chars)
      "recipients": ["vbhvverma7@gmail.com"]
    }

Get a Gmail App Password:  Google Account -> Security -> 2-Step Verification (on)
-> App passwords -> create one for "Mail". Paste the 16-char code above.

Usage:
    python alerts.py            # send digest for any new pathology posts
    python alerts.py --baseline # mark all current listings as already-sent (run once)
    python alerts.py --test     # send a test email to confirm credentials work
"""
import json
import os
import smtplib
import ssl
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import db

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "email_config.json")


def load_config():
    # Env-var override (handy for cron); else the json file.
    if os.environ.get("PATHO_SMTP_APP_PASSWORD"):
        return {
            "smtp_host": os.environ.get("PATHO_SMTP_HOST", "smtp.gmail.com"),
            "smtp_port": int(os.environ.get("PATHO_SMTP_PORT", "465")),
            "sender": os.environ["PATHO_SMTP_SENDER"],
            "app_password": os.environ["PATHO_SMTP_APP_PASSWORD"],
            "recipients": os.environ.get("PATHO_SMTP_RECIPIENTS", os.environ["PATHO_SMTP_SENDER"]).split(","),
        }
    if not os.path.exists(CONFIG_PATH):
        return None
    with open(CONFIG_PATH) as f:
        return json.load(f)


def _send(cfg, subject, html):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Pathology Tracker <{cfg['sender']}>"
    msg["To"] = ", ".join(cfg["recipients"])
    msg.attach(MIMEText("Open in an HTML-capable mail client.", "plain"))
    msg.attach(MIMEText(html, "html"))
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"], context=ctx) as s:
        s.login(cfg["sender"], cfg["app_password"].replace(" ", ""))
        s.sendmail(cfg["sender"], cfg["recipients"], msg.as_string())


def _digest_html(items):
    by_region = {}
    for it in items:
        by_region.setdefault(it["region"], []).append(it)
    blocks = []
    for region in sorted(by_region):
        rows = ""
        for it in by_region[region]:
            snip = f"<div style='color:#555;font-size:13px;margin-top:3px'>{it['snippet']}</div>" if it.get("snippet") else ""
            rows += f"""
            <div style="padding:12px 0;border-bottom:1px solid #eee">
              <a href="{it['url']}" style="font-size:15px;font-weight:600;color:#1a56db;text-decoration:none">{it['title']}</a>
              <div style="color:#777;font-size:12px;margin-top:3px">{it['source_name']} · {it['category']}</div>
              {snip}
            </div>"""
        blocks.append(f"""
          <h3 style="margin:22px 0 4px;color:#111;font-size:14px;text-transform:uppercase;letter-spacing:.5px">
            {region} <span style="color:#999;font-weight:400">({len(by_region[region])})</span></h3>
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
    cfg = load_config()
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
    cfg = load_config()
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
    db.init_db()
    if "--baseline" in sys.argv:
        n = db.baseline_mark_all_alerted()
        print(f"Baseline set: {n} existing listings marked as already-alerted. "
              "Future scrapes will only email genuinely new pathology posts.")
    elif "--test" in sys.argv:
        send_test()
    else:
        send_new()
