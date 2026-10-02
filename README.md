# 🔬 Pathology SR-Exam & Jobs Tracker

A self-updating dashboard that watches official recruitment/career pages for
**Senior Resident exams, pathology jobs, and paid fellowships** across
HP · Punjab · Haryana · Chandigarh · Delhi · Uttarakhand · Kerala · J&K · UP ·
the INIs (AIIMS network, PGIMER, JIPMER, NIMHANS) · top metros (Mumbai,
Ahmedabad) · and paid fellowships. MD (Human) Pathology only — dental, oral
and veterinary notices are filtered out at scrape time. Built for your MD
Pathology job hunt (degree completes 7 Oct 2026).

## Open it in Chrome
The dashboard now runs as a background service — just visit **http://localhost:5000**.

```
systemctl --user status pathology-dashboard.service    # check it
systemctl --user restart pathology-dashboard.service   # restart after code edits
```
(Manual fallback: `cd ~/pathology-jobs-dashboard && ./venv/bin/python app.py`.)

## How it works
- **`sources.py`** – the 45 official pages that get polled. Add/remove freely.
- **`scraper.py`** – fetches each page, keeps anything mentioning pathology /
  senior resident / fellowship / recruitment. Each source is isolated, so one
  dead site never breaks the run. Stores into `data.db` (SQLite).
- **`seed.py`** – 21 hand-verified opportunities (with pay & eligibility notes)
  so the board is useful immediately and covers bot-blocked sites. Every seeded
  `source_id` must also exist in `sources.SOURCES`, or the seed silently
  resurrects a retired source on the next daily run
  (`tests/test_seed_alignment.py`).
- **`app.py`** – Flask dashboard + JSON API (`/api/data`, `/api/refresh`, `/api/flag`).
- **`templates/dashboard.html`** – the UI: region/category filters, full-text
  search, "New 7d" + relevance toggles, ⭐ star and ✕ hide, live source-health panel.

## 📱 Mobile access (Vercel)
A static mirror is deployed to **https://pathology-tracker.vercel.app** — open it
on your phone. It shows the same data, updated daily. Star/hide on the mirror are
saved in that browser (local storage). "Update now" only works on the desktop app.

The daily pipeline rebuilds `public/` and redeploys it automatically. To redeploy
by hand: `cd public && vercel deploy --prod --yes`.

## 📧 Email alerts for NEW pathology posts
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
prune stale listings → **re-sync the opportunity projection** → rebuild static →
redeploy to Vercel. Logs go to `scrape.log` (auto-trimmed). The UI shows a ⚠️
banner whenever data is older than 24 h.

## Scraping feedback

`↻ Update now` is **POST** `/api/refresh` and takes ~1 minute. The button
shows live elapsed time while it runs, then reports what the scrape actually
did: how many listings were new, total, and how many sources were OK vs
failed. Those counters come from `/api/data` `stats` (`last_run_new`,
`last_run_ok`, `last_run_failed`, `projection_ok`, `orphans`). If a scrape
crashes, the UI says so within 3 minutes instead of spinning forever.

Most refreshes legitimately add **0–2** new listings — if the list looks
unchanged that is usually the honest result, not a failure. Source health
(the live panel) is the real signal.

## Finding notices on linked documents

Some institutions do not publish a notice on their homepage. AIIMS Jodhpur is
the clearest example: the home page links to a rolling
`residents-rec.php` page, that page links to the actual Senior Resident
advertisement as a bare **"View Document"** anchor, and the PDF lives on a
*different host* (`rec.aiimsjodhpur.edu.in`). The word "Pathology" appears only
inside the PDF's department table — nowhere in any link text or URL.

`scraper.py` therefore adds a narrow **document-discovery** layer:

1. After the homepage, it follows up to 3 same-site index pages whose link text
   or href looks like a recruitment/notice page. Same-site is judged on the
   *registrable* domain, so `rec.aiimsjodhpur.edu.in` is reachable from
   `aiimsjodhpur.edu.in` but `pmssy.mohfw.gov.in` is not.
2. It downloads up to 12 linked PDFs (12 MB / 8 page cap each), extracts the
   text, and if the body mentions a pathology department it **promotes the
   listing to `high` relevance**, replaces the "View Document" title with the
   notice's real `Subject:` line, and fills the snippet, deadline and document
   type from the body. It only ever adds signal — never demotes.
3. Because the departmental vacancy table is what a pathologist needs, the
   snippet is the line around the pathology keyword, e.g.
   `Pathology & Lab Medicine MD/DNB in Pathology 1 1 0 0 0 2`.

Requires `pypdf` (in `requirements.txt`). Without it the scraper degrades to
the previous homepage-only behaviour rather than failing. Two supporting
changes make this work: `JUNK_TITLES` gained the `view document` family so the
stub never reaches the card, and `_candidates` exempts PDF targets from that
junk filter — otherwise the one link worth following is the one link dropped.

## Tests & CI
`./venv/bin/python -m pytest tests/ -q` — 271 tests, no network, ~14 s.

`.github/workflows/daily-scrape.yml` runs a `test` job (compile + pytest) that the
scheduled `scrape` job depends on, so a failing test blocks the publish instead
of shipping a bad static mirror. The scrape job also re-verifies the
opportunity/listing projection invariant and fails the build rather than
committing drifting data — see ARCHITECTURE.md §4.

`tests/test_dashboard_ui.py` is the UI gate. It parses `templates/dashboard.html`
and asserts the design contract that is invisible at runtime: every CSS custom
property is defined and used, WCAG AA contrast recomputed from the `:root`
values, no duplicate element ids, one `<h1>` and correct heading order, source
failures rendered in the page rather than hidden behind a `title=` tooltip, and
the card animation gated to first paint. Where a function is pure, the real
source is extracted and executed under `node`, so `safeUrl`, `esc`, `cleanErr`,
`tierOf`, `daysUntil` and `scrapeSummary` are tested on their actual bodies
rather than on a copy. `node` is optional — those tests skip without it.

`tests/test_document_discovery.py` guards the document-discovery layer
offline: the site-scope and index-following logic, the PDF body extractors
(subject, deadline, pathology sentence) against a reduced fixture of the real
AIIMS Jodhpur advertisement, the link-furniture trimmer, and the "only ever add
signal, never demote" promotion policy.

## Dashboard tips
- **Pathology only** filter (relevance = high) = items that actually name
  pathology / haematology / cytology / histopathology / transfusion / lab medicine.
- **Recruitment notices** (medium) = SR/walk-in/vacancy notices that may include
  pathology — open them to confirm.
- ⭐ a listing to build your shortlist; ✕ to hide noise. Flags persist.
- The **source-health panel** shows which sites are live and how many items each returned.

## Known bot-blocked sources (covered by verified seed cards instead)
BFUHS Faridkot (WAF, 403 to all scripts). Its seed card carries the correct apply
link — open and check the live notice. The National Board (natboard) is likewise
WAF-blocked from this host (HTTP 403 on both http and https), so its stored
cards drift stale — open the card to check the live notice. UHS Rohtak was
fixed by pointing at its new URL; flaky govt sites (PGIMER, tmc.gov.in) are
handled with automatic retries.

## Add a new source
Append a dict to `SOURCES` in `sources.py`:
```python
{"id": "myinst", "name": "My Institute", "region": "Delhi",
 "category": "Govt – Senior Resident", "url": "https://…/recruitment"},
```
Then run `./venv/bin/python scraper.py`.

> ⚠️ Always confirm dates/eligibility on the official source page before applying.
> This tool surfaces notices; it does not replace the official advertisement.
