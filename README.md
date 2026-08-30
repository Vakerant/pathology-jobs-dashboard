# 🔬 Pathology SR-Exam & Jobs Tracker

A self-updating dashboard that watches official recruitment/career pages for
**Senior Resident exams, pathology jobs, and paid fellowships** across
HP · Punjab · Haryana · Chandigarh · Delhi · the INIs (AIIMS network, PGIMER,
JIPMER, NIMHANS) · top metros (Mumbai, Ahmedabad) · and private chains
(Dr Lal, Metropolis, Agilus). Built for your MD Pathology job hunt (degree
completes 7 Oct 2026).

## Open it in Chrome
The dashboard now runs as a background service — just visit **http://localhost:5000**.

```
systemctl --user status pathology-dashboard.service    # check it
systemctl --user restart pathology-dashboard.service   # restart after code edits
```
(Manual fallback: `cd ~/pathology-jobs-dashboard && ./venv/bin/python app.py`.)

## How it works
- **`sources.py`** – the ~34 official pages that get polled. Add/remove freely.
- **`scraper.py`** – fetches each page, keeps anything mentioning pathology /
  senior resident / fellowship / recruitment. Each source is isolated, so one
  dead site never breaks the run. Stores into `data.db` (SQLite).
- **`seed.py`** – 24 hand-verified opportunities (with pay & eligibility notes)
  so the board is useful immediately and covers bot-blocked sites.
- **`app.py`** – Flask dashboard + JSON API (`/api/data`, `/api/refresh`, `/api/flag`).
- **`templates/dashboard.html`** – the UI: region/category filters, full-text
  search, "New 7d" + relevance toggles, ⭐ star and ✕ hide, live source-health panel.

## 📱 Mobile access (Vercel)
A static mirror is deployed to **https://pathology-tracker.vercel.app** — open it
on your phone. It shows the same data, updated daily. Star/hide on the mirror are
saved in that browser (local storage). "Update now" only works on the desktop app.

The daily pipeline rebuilds `public/` and redeploys it automatically. To redeploy
by hand: `cd public && vercel deploy --prod --yes`.

## 📧 Email alerts for NEW pathology posts  → vbhvverma7@gmail.com
You get an email **only when a genuinely new pathology post appears** (high-relevance
items: pathology/haematology/cytology/histopathology/transfusion/lab-medicine).
Already-seen posts never re-alert.

**One-time setup (needs a Gmail App Password):**
1. Turn on 2-Step Verification: Google Account → Security.
2. Create an App Password: Security → App passwords → app "Mail" → copy the 16 chars.
3. ```
   cd ~/pathology-jobs-dashboard
   cp email_config.example.json email_config.json
   # edit email_config.json -> paste the 16-char password into "app_password"
   chmod 600 email_config.json
   ./venv/bin/python alerts.py --test      # sends a test email
   ```
Until you do this, alerts are simply skipped (everything else still runs).

## Daily auto-update (three layers, so it actually stays fresh)
1. **systemd user timer** — runs `run_daily.sh` at **08:00 daily** and, unlike the
   old cron job, **catches up automatically if the laptop was off/asleep**
   (`Persistent=true`). Check: `systemctl --user list-timers | grep pathology`.
2. **In-app auto-scrape** — while the dashboard service runs, it re-scrapes on its
   own whenever the data is older than 6 h.
3. **↻ Update now** — on-demand from the dashboard.

The pipeline: seed → scrape (parallel, with retries) → email new pathology posts →
prune stale listings → rebuild static → redeploy to Vercel. Logs go to `scrape.log`
(auto-trimmed). The UI shows a ⚠️ banner whenever data is older than 24 h.

## Dashboard tips
- **Pathology only** filter (relevance = high) = items that actually name
  pathology / haematology / cytology / histopathology / transfusion / lab medicine.
- **Recruitment notices** (medium) = SR/walk-in/vacancy notices that may include
  pathology — open them to confirm.
- ⭐ a listing to build your shortlist; ✕ to hide noise. Flags persist.
- The **source-health panel** shows which sites are live and how many items each returned.

## Known bot-blocked sources (covered by verified seed cards instead)
BFUHS Faridkot (WAF, 403 to all scripts). Its seed card carries the correct apply
link — open and check the live notice. UHS Rohtak and Agilus were fixed by
pointing at their new URLs; flaky govt sites (PGIMER, tmc.gov.in) are handled
with automatic retries.

## Add a new source
Append a dict to `SOURCES` in `sources.py`:
```python
{"id": "myinst", "name": "My Institute", "region": "Delhi",
 "category": "Govt – Senior Resident", "url": "https://…/recruitment"},
```
Then run `./venv/bin/python scraper.py`.

> ⚠️ Always confirm dates/eligibility on the official source page before applying.
> This tool surfaces notices; it does not replace the official advertisement.
