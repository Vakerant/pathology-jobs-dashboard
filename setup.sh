#!/usr/bin/env bash
# One-shot setup for a fresh clone of the pathology jobs dashboard.
#
# Installs: python venv + dependencies, the database schema, the first seed,
# and a systemd USER timer that runs run_daily.sh every morning.
#
# Why systemd and not cron: this project previously installed a
# "30 7 * * *" crontab line here. The project has since moved to systemd user
# units, so that cron block double-scheduled the daily scrape for anyone who
# ran this script — two concurrent scrapes fighting over the same SQLite file.
# Do not reintroduce it.
set -euo pipefail
cd "$(dirname "$0")"

REPO_DIR="$(pwd)"

echo "==> Creating virtualenv"
python3 -m venv venv
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt

echo "==> Initialising database + seeding verified opportunities"
./venv/bin/python seed.py

echo "==> Running the first scrape (network; safe to skip with Ctrl-C)"
./venv/bin/python scraper.py || echo "   (first scrape incomplete — the daily timer will retry)"

chmod +x run_daily.sh

# ---------------------------------------------------------------------------
# systemd user units
# ---------------------------------------------------------------------------
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

write_unit() {
  local name="$1" body="$2"
  mkdir -p "$UNIT_DIR"
  if [ -e "$UNIT_DIR/$name" ] && ! cmp -s "$UNIT_DIR/$name" "$body"; then
    local backup="$UNIT_DIR/$name.bak.$(date +%s)"
    cp "$UNIT_DIR/$name" "$backup"
    echo "   existing $name differs; saved to $backup"
  fi
  cp "$body" "$UNIT_DIR/$name"
}

SCRAPE_UNIT="$(mktemp)"
cat > "$SCRAPE_UNIT" <<UNIT
[Unit]
Description=Pathology jobs tracker - daily scrape, alerts, static export, Vercel deploy
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$REPO_DIR
ExecStart=$REPO_DIR/run_daily.sh
TimeoutStartSec=30min
UNIT

TIMER_UNIT="$(mktemp)"
cat > "$TIMER_UNIT" <<UNIT
[Unit]
Description=Daily pathology jobs scrape (catches up if the machine was off)

[Timer]
OnCalendar=*-*-* 08:00
Persistent=true
RandomizedDelaySec=10min

[Install]
WantedBy=timers.target
UNIT

DASH_UNIT="$(mktemp)"
cat > "$DASH_UNIT" <<UNIT
[Unit]
Description=Pathology jobs dashboard (Gunicorn, http://localhost:5000)
After=network-online.target

[Service]
WorkingDirectory=$REPO_DIR
ExecStart=$REPO_DIR/venv/bin/gunicorn --bind 0.0.0.0:5000 --workers 2 --timeout 60 --access-logfile - --error-logfile - app:app
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
UNIT

if command -v systemctl >/dev/null 2>&1; then
  echo "==> Installing systemd user units into $UNIT_DIR"
  write_unit pathology-scrape.service "$SCRAPE_UNIT"
  write_unit pathology-scrape.timer   "$TIMER_UNIT"
  write_unit pathology-dashboard.service "$DASH_UNIT"

  systemctl --user daemon-reload
  systemctl --user enable --now pathology-scrape.timer
  echo "   daily scrape timer:  systemctl --user list-timers pathology-scrape.timer"
  echo
  echo "   To also run the dashboard as a service (recommended):"
  echo "     systemctl --user enable --now pathology-dashboard.service"
  echo "     loginctl enable-linger \"\$(whoami)\"   # keep it running after logout"
else
  echo "==> systemd not found — not installing units."
  echo "   Schedule run_daily.sh yourself (cron, launchd, CI, ...)."
  echo "   Do NOT also add the old '30 7 * * *' cron line alongside a systemd timer."
fi

rm -f "$SCRAPE_UNIT" "$TIMER_UNIT" "$DASH_UNIT"

echo
echo "✅ Done."
echo "   Dashboard (foreground):  ./venv/bin/python app.py"
echo "   Dashboard (service):     systemctl --user start pathology-dashboard.service"
echo "   One-off scrape:          ./run_daily.sh && tail -f scrape.log"
echo "   Tests:                   ./venv/bin/python -m pytest tests/ -q"
