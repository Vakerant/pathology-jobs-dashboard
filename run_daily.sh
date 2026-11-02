#!/usr/bin/env bash
# Daily pipeline (invoked by the systemd user timer pathology-scrape.timer,
# NOT cron -- `crontab -l` is empty on purpose). Logs to scrape.log.
#   1. refresh verified seeds        2. scrape live notices
#   3. email alert for NEW pathology 4. rebuild static site
#   5. (the live site is deployed by Vercel from GitHub, not from here)
#
# Exit status is non-zero if ANY step failed, so the systemd unit reports a
# failed run instead of silently claiming success. Previously every step ran
# unconditionally and the block always exited 0: a failed scraper still sent
# alerts from stale data, still exported a mirror, and still redeployed to
# Vercel, with the failure buried somewhere in a 400KB log nobody reads.
#
# A scrape failure is treated as fatal-on-purpose but still lets the alerting
# and export steps run, so a good scrape is never wasted because the previous
# one wobbled; only the scrape step's own status gates the exit code.
cd "$(dirname "$0")" || exit 1
export PATH="$HOME/.npm-global/bin:$HOME/.local/bin:/usr/local/bin:/usr/bin:$PATH"
PY="./venv/bin/python"
[ -x "$PY" ] || PY="python3"

# keep the log from growing forever
if [ -f scrape.log ] && [ "$(stat -c%s scrape.log)" -gt 500000 ]; then
  tail -c 200000 scrape.log > scrape.log.tmp && mv scrape.log.tmp scrape.log
fi

# Accumulated step failures; reported at the end so the unit's status is honest.
rc=0

run_step() {
  local label="$1"; shift
  if "$@"; then
    echo "  [OK]   $label"
  else
    local step_rc=$?
    echo "  [FAIL] $label (exit $step_rc)"
    rc=1
  fi
}

{
  echo "===== $(date) ====="

  run_step "seed"        "$PY" seed.py
  run_step "scrape"      "$PY" scraper.py
  run_step "alerts"      "$PY" alerts.py            # emails only genuinely-new pathology posts
  run_step "export"      "$PY" export_static.py     # rebuild public/ (data.json + index.html)

  # No Vercel deploy here: Vercel auto-deploys from GitHub, whose daily workflow
  # publishes public/. Deploying from this laptop too made the site flip between
  # two datasets.

  if [ "$rc" -eq 0 ]; then
    echo "===== pipeline OK ====="
  else
    echo "===== pipeline COMPLETED WITH FAILURES (exit $rc) ====="
  fi
  echo ""
} >> scrape.log 2>&1

exit "$rc"
