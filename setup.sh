#!/usr/bin/env bash
# One-time setup: venv, deps, first scrape, daily cron job at 07:30.
set -e
cd "$(dirname "$0")"

echo "→ Creating virtual environment…"
python3 -m venv venv
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt

echo "→ Seeding verified opportunities…"
./venv/bin/python seed.py

echo "→ First live scrape (this hits ~30 sites, ~1–2 min)…"
./venv/bin/python scraper.py || true

chmod +x run_daily.sh

echo "→ Installing daily cron job (07:30 every day)…"
CRON_LINE="30 7 * * * $(pwd)/run_daily.sh"
( crontab -l 2>/dev/null | grep -v "$(pwd)/run_daily.sh" ; echo "$CRON_LINE" ) | crontab -
echo "   Installed: $CRON_LINE"

echo ""
echo "✅ Done. Start the dashboard with:  ./venv/bin/python app.py"
echo "   then open  http://localhost:5000  in Chrome."
