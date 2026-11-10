"""
Build the static `public/` folder that gets deployed to Vercel for mobile access.

Reads the local SQLite DB and writes:
  public/index.html  (the dashboard — same UI, auto-detects static mode)
  public/data.json   ({listings, status, stats})

Run after every scrape. The local cron then redeploys public/ to Vercel.
"""
import json
import os
import shutil

import db

HERE = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(HERE, "public")


def build():
    db.init_db()
    os.makedirs(PUBLIC, exist_ok=True)

    listings = db.fetch_listings()
    status = db.fetch_status()
    db.enrich(listings)

    visible = [l for l in listings if not l["hidden"]]
    active_path = [l for l in visible if l["relevance"] == "high" and not l["expired"]]
    stats = {
        "total": len(visible),
        "new_week": sum(1 for l in visible if l["is_new"]),
        "high": sum(1 for l in visible if l["relevance"] == "high"),
        "active_pathology": len(active_path),
        "closing_soon": sum(1 for l in visible if l.get("closing_soon")),
        "expired": sum(1 for l in visible if l["expired"]),
        "starred": sum(1 for l in visible if l["starred"]),
        "sources_ok": sum(1 for s in status if s["status"] == "ok"),
        "sources_failed": sum(1 for s in status if s["status"] == "failed"),
        "last_run": db.get_meta("last_run"),
        "scraping": False,
    }
    payload = {"listings": listings, "status": status, "stats": stats}

    # Write atomically. A plain open+json.dump leaves a truncated file if the
    # process dies mid-write, and this file is the entire data source for the
    # deployed mirror — a torn write publishes a broken dashboard. Write to a
    # sibling temp file and os.replace() it in, which is atomic on POSIX.
    data_path = os.path.join(PUBLIC, "data.json")
    tmp_path = data_path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())   # durable before the rename swaps it in
    os.replace(tmp_path, data_path)

    # The dashboard template doubles as the static page (it auto-detects).
    shutil.copyfile(os.path.join(HERE, "templates", "dashboard.html"),
                    os.path.join(PUBLIC, "index.html"))

    print(f"Static site built in public/  ({len(listings)} listings, "
          f"{stats['high']} pathology, {stats['sources_ok']} sources OK).")


if __name__ == "__main__":
    import config
    config.setup_logging()
    build()
