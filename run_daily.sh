#!/usr/bin/env bash
# Daily pipeline (invoked by the systemd user timer pathology-scrape.timer,
# NOT cron -- `crontab -l` is empty on purpose). Logs to scrape.log.
#   0. fast-forward to origin/main   1. seed (no-op, kept for CI parity)
#   2. scrape live notices           3. email alert for NEW pathology
#   4. rebuild static site           5. push public/ to GitHub -> Vercel deploys
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

# The generated mirror. Only these two files are ever committed by this script.
PUBLISHED="public/data.json public/index.html"

on_main() { [ "$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" = "main" ]; }

# Run today's code: fast-forward to origin/main before scraping, so a fix
# pushed to GitHub reaches this machine's next run. The mirror files are put
# back to the committed version first (they are rebuilt below anyway). Skipped,
# not failed, when someone is working on another branch here.
update_code() {
  if ! on_main; then
    echo "    not on main ($(git rev-parse --abbrev-ref HEAD)); running the checked-out code"
    return 0
  fi
  git restore --source=HEAD --staged --worktree -- $PUBLISHED 2>/dev/null
  git pull -q --rebase origin main
}

# Commit the rebuilt mirror and push it to main. "[skip ci]" keeps GitHub from
# re-running its pipeline for a data-only commit. `git commit -- <paths>`
# commits only the mirror, never anything else that happens to be staged.
# If origin moved meanwhile, rebase onto it; -X theirs keeps this run's data
# (in a rebase, "theirs" is the commit being replayed: ours from this machine).
publish() {
  if ! on_main; then
    echo "    not on main; not publishing"
    return 0
  fi
  git add -f -- $PUBLISHED
  if git diff --cached --quiet -- $PUBLISHED; then
    echo "    mirror unchanged; nothing to publish"
    return 0
  fi
  local n
  n=$("$PY" -c "import json; print(len(json.load(open('public/data.json'))['listings']))" 2>/dev/null || echo "?")
  git commit -q -m "chore: laptop scrape $n listings [skip ci]" -- $PUBLISHED || return 1
  local i
  for i in 1 2 3; do
    if git pull -q --rebase -X theirs origin main && git push -q origin HEAD:main; then
      echo "    pushed $n listings to GitHub; Vercel deploys from there"
      return 0
    fi
    sleep 20
  done
  return 1
}

run_step() {
  local label="$1"; shift
  if "$@"; then
    echo "  [OK]   $label"
  else
    local step_rc=$?
    echo "  [FAIL] $label (exit $step_rc)"
    rc=1
    return "$step_rc"
  fi
}

{
  echo "===== $(date) ====="

  run_step "update code" update_code
  run_step "seed"        "$PY" seed.py
  if run_step "scrape"   "$PY" scraper.py; then scrape_ok=1; else scrape_ok=0; fi
  run_step "alerts"      "$PY" alerts.py            # emails only genuinely-new pathology posts
  if run_step "export"   "$PY" export_static.py; then export_ok=1; else export_ok=0; fi

  # Publish: this laptop is the scraper of record. Many Indian government sites
  # (AIIMS Bhopal, Jodhpur, Guwahati, Rajkot, ...) time out for the US-hosted
  # GitHub runners but answer from here, so the published data comes from this
  # machine. It is pushed to GitHub and Vercel deploys it from there -- one
  # deploy path, so the site cannot flip between two datasets. GitHub's own
  # scheduled scrape only runs as a fallback when this push is >36h old.
  if [ "$scrape_ok" -eq 1 ] && [ "$export_ok" -eq 1 ]; then
    run_step "publish"   publish
  else
    echo "  [SKIP] publish (scrape or export failed; the live site keeps the last good data)"
  fi

  if [ "$rc" -eq 0 ]; then
    echo "===== pipeline OK ====="
  else
    echo "===== pipeline COMPLETED WITH FAILURES (exit $rc) ====="
  fi
  echo ""
} >> scrape.log 2>&1

exit "$rc"
