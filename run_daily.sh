#!/usr/bin/env bash
# Daily pipeline (invoked by cron). Logs to scrape.log.
#   1. refresh verified seeds        2. scrape live notices
#   3. email alert for NEW pathology 4. rebuild static site
#   5. redeploy mirror to Vercel (mobile)
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:$PATH"
PY="./venv/bin/python"
[ -x "$PY" ] || PY="python3"

# keep the log from growing forever
if [ -f scrape.log ] && [ "$(stat -c%s scrape.log)" -gt 500000 ]; then
  tail -c 200000 scrape.log > scrape.log.tmp && mv scrape.log.tmp scrape.log
fi

{
  echo "===== $(date) ====="
  "$PY" seed.py
  "$PY" scraper.py
  "$PY" alerts.py            # emails only genuinely-new pathology posts
  "$PY" export_static.py     # rebuild public/ (data.json + index.html)

  # Redeploy the static mirror to Vercel for mobile access.
  if command -v vercel >/dev/null 2>&1; then
    ( cd public && vercel deploy --prod --yes ) && echo "Vercel redeploy OK"
  else
    echo "vercel CLI not on PATH — skipped redeploy"
  fi
  echo ""
} >> scrape.log 2>&1
