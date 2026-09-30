# Architecture — Pathology Career Intelligence Platform

This document records the architecture **before** and **after** the
opportunity-first transformation, the migration that carried the existing
database forward without loss, and the intended direction of the remaining
phases. It is a Phase 0 audit artifact kept in sync with the code.

## 1. Before (original architecture)

```
source registry (sources.py, 34 sources)
        │
        ▼
scraper.py  (generic HTML link scraper, parallel threads, keyword relevance)
        │
        ▼
SQLite data.db  ── listings / source_status / meta
        │
        ▼
Flask app.py  ── / (dashboard) /api/data /api/refresh /api/flag
        │
        ▼
templates/dashboard.html ── export_static.py ── public/ ── Vercel mirror
```

Supporting pieces: `alerts.py` (email digest), `seed.py` (24 hand-verified
seeds), `run_daily.sh`, GitHub Action `daily-scrape.yml` (08:00 IST).

**Known flaws at baseline**

- **Listing-first** — the unit of record was a scraped link, not an opportunity.
- **Generic relevance** — keyword matching on link text only.
- **Corrigendum/addendum/extension dropped** — these were in the `NEGATIVE`
  filter and discarded instead of linked to their parent opportunity.
- **No tests, no migrations, no secrets hygiene** — a personal Gmail address
  was hardcoded in alerts/README/example-config; no `.env`; `data.db` mutated
  in place with no versioned schema.

## 2. After (current architecture)

```
sources.py (registry) ──► scraper.py (fetch + classify document_type)
                                   │
                                   ▼
                             db.py (listings, scrape/flag plumbing)
                                   │
                                   ▼
                       repository.py (opportunity-first service layer)
                                   │
             ┌─────────────────────┼──────────────────────┐
             ▼                     ▼                      ▼
      opportunities          documents (versioned)    institutions
             │                     │
             ▼                     ▼
          events            document_versions
             │
             ▼
   applications / user_profile
                                   │
                                   ▼
                          app.py (Flask API + dashboard)
```

New capability layers (all lossless, additive — `listings` still exists):

| Layer | File | Responsibility |
|-------|------|----------------|
| Config | `config.py` | env-driven config, no secrets |
| Migrations | `migrations.py` | versioned schema runner (backup → validate → migrate → verify) |
| Domain model | `migrations.py` #2 | 9 new tables (see §3) |
| Service layer | `repository.py` | opportunity-first access, institution derivation, projection sync + orphan reconciliation (driven by `scraper.run()`, see §4) |
| Document discovery | `scraper.py` | follows same-site index pages and enriches listings from linked notice PDFs (see §2.1) |
| Tests | `tests/` | 215 tests across date/relevance/dedup/enrich/document_type/repository/app-scrape-lock/dashboard-ui/document-discovery |

## 2.1 Document discovery (added 2026-09-26)

The pre-§2 scraper read one page per source and judged relevance from link text
and href alone. That is sufficient only when an institution publishes the notice
on the page you land on. It is not sufficient in the common case, and the gap is
silent: no error, no failed source, just a missing posting.

**The case that forced this.** AIIMS Jodhpur's homepage links Senior Resident
notices to a rolling `residents-rec.php` page. That page links the actual
advertisement as a bare `View Document` anchor, and the PDF is served from a
*different host*, `rec.aiimsjodhpur.edu.in`. The departmental vacancy table —
the only place "Pathology" appears at all — is inside the PDF. The homepage
scraper therefore could not see the notice no matter how many times it ran, and
every Jodhpur row it did capture was `medium` relevance, invisible under the
dashboard's default `rel=high` view.

The layer is deliberately narrow rather than a general crawler:

- **Index following** — up to `MAX_INDEX_FOLLOWS = 3` same-site pages whose
  anchor text or href matches `_INDEX_HINT` (recruit/vacan/career/notice/
  senior-resident/…). Same-site is `_registrable(hostname)`, so subdomain
  variants of one institution are in scope while a link to another ministry
  (`pmssy.mohfw.gov.in`) is not. Links are taken in document order, and the
  `MAX_ITEMS_PER_SOURCE` budget is applied *after* merging so a link-dense index
  page cannot starve the homepage.
- **PDF enrichment** — up to `MAX_PDF_ENRICH = 12` documents per source, each
  capped at 12 MB / 8 pages. If the body mentions a pathology department the
  listing is promoted to `high`, the stub title is replaced with the notice's
  real `Subject:` line, and the snippet, deadline and document type are filled
  from the body. **Enrichment only ever adds signal; it never demotes a
  listing**, so it cannot hide something the homepage pass already found.
- **Everything is bounded and swallowed.** Per-index and per-document failures
  are logged and skipped. A government site returning a corrupt PDF degrades
  that one listing, never the source.

Two design points worth recording because the obvious implementation is wrong:

1. **Promotion must not reuse `relevance()`.** `relevance()` applies a negative
   gate, and an advertisement body trips it constantly — the real Jodhpur
   document matches ten negative tokens (`test`, `result`, `biochem`,
   `radiolog`, `guideline`, …) purely because it lists every other department
   and the written exam. `relevance(body)` returns `None`. The body check is
   therefore `_mentions_pathology()`: pathology keywords, no negative gate.
2. **PDF text needs its own deadline window.** `extract_dates()` looks ~50
   characters back for a deadline label; in the PDF the label "last date" sits
   ~76 characters from the date, so the shared extractor returns `(None, None)`.
   `_pdf_deadline()` widens that window to 120 characters and reuses
   `_iter_dates()`. `extract_dates()` itself is unchanged and still shared with
   the HTML path.

Supporting changes: `JUNK_TITLES` gained the `view document` family so the stub
never reaches a card, and `_candidates` now exempts PDF targets from that filter
— otherwise the one link worth following is the one link dropped. The date
regex also learned ordinals (`5th October, 2026`), which Indian notices use
almost exclusively; this widened recall for HTML listings too and was checked
against the existing false positives (`regd. No. 12345 of 2026`, `Room 210,
2026`) which still do not match.

`pypdf` is an optional dependency. It is in `requirements.txt` so CI exercises
this path, but the import is guarded — without it the scraper still runs and
simply scrapes less deeply rather than failing.

## 3. Domain model (schema v2 → v3)

Introduced in migration #2, all tables coexist with legacy `listings`:

- **institutions** — institution_id PK, name, state, departments/locations/sources
  (JSON), reliability class (GOVT/INI/FELLOWSHIP/PRIVATE), created_at.
- **opportunities** — canonical identity for a recruitment event; id = legacy
  listing key during Phase 2 (1:1 lossless projection). Fields: institution,
  department, role, specialty/sub_specialty, location, state, region, category,
  employment_type, vacancy_count, qualification, experience, age_limit, salary,
  stipend, application_mode/url, notice_date, deadline, interview_date, fee,
  documents_required, relevance, eligibility_state (default
  INSUFFICIENT_INFORMATION), lifecycle_state (default ACTIVE), source/source_name,
  title/url/snippet, is_seed/starred/hidden, first_seen/last_seen, discovered_at,
  last_verified_at, evidence (JSON).
- **documents** — document_id = sha1(source_id|url); source_id, url,
  canonical_url, content_type, title, retrieved_at/published_at, content_hash,
  raw_text/extracted_text, parser/parser_version, http_status, freshness,
  document_type (ADVERTISEMENT|CORRIGENDUM|ADDENDUM|EXTENSION|INTERVIEW_NOTICE|
  SHORTLIST|RESULT|CANCELLATION|RECRUITMENT_RULE|UNKNOWN), opportunity_id.
- **document_versions** — version_id = sha1(document_id|content_hash); per-hash
  history so unchanged content never re-runs extraction.
- **events** — event_id, event_type, opportunity_id, occurred_at, payload (JSON),
  idempotency_hash UNIQUE.
- **scrape_runs** / **source_runs** — per-run and per-source observability.
- **user_profile** — singleton (id=1 CHECK), never hardcodes the author.
- **applications** — per-opportunity lifecycle (SAVED…NOT_ELIGIBLE).

`listings` gained a `document_type` column (migration #3) so the scraper's
classification survives into storage without clobbering prior values
(UNKNOWN never overwrites a classified value).

## 4. Migration story

- **Baseline v1** — legacy schema adopted in place (never rebuilt).
- **v2** — Phase 2 domain tables created + `listings → opportunities` backfilled
  1:1 + documents seeded per URL.
- **v3** — `listings.document_type` added.

Runner guarantees: backup to `backups/data-<UTC>.db` before any pending
migration, `PRAGMA integrity_check` before/after, per-migration transactions
with rollback, and a `_verify` invariant — see "Projection invariant" below.

Result on the production `data.db` (lossless): listings 1128, opportunities
1128 (identical key sets), documents 1061 (per-URL collapse), institutions 34,
source_status 34, meta 8 — schema_version 3, integrity_check ok.
*(That snapshot is the state immediately after the v3 backfill. Live counts have
since drifted; see "Post-migration projection repair" for the current figures.)*

### Projection invariant (refined after the 2026-09 audit)

The original `_verify` check asserted `opportunities count == listings count`.
That turned out to be an unenforceable invariant, because the retention pruner
only removes rows from `listings` while `opportunities` is an append-only
projection that deliberately never deletes (per §6: historical rows are
superseded, not deleted). Rows pruned out of `listings` were therefore
accumulating in `opportunities` as silent orphans, and new listings were never
reaching `opportunities` at all — the projection was frozen at migration time
because nothing in the production pipeline called `repository.py` (only its
tests did).

The invariant is now:

- `listings == live opportunities` (opportunities whose `lifecycle_state` is not
  `SUPERSEDED`), **and**
- `orphans == 0` (opportunities with no matching `listings` row that are not
  `SUPERSEDED`), **and**
- `0 ≤ documents ≤ listings`.

Total `opportunities` may now legitimately *exceed* `listings`, because
superseded rows are retained as history. `repository.verify_projection()` returns
exactly these counts plus the boolean `ok`.

### Post-migration projection repair

`scraper.run()` now drives the projection on every scrape, after pruning:

1. `repository.sync_opportunities_from_listings()` — upserts every `listings`
   row into `opportunities`, reviving rows that were `SUPERSEDED` if the source
   reappears. Human-set states (`CLOSED`/`CANCELLED`/`ARCHIVED`) are never
   clobbered by the sync.
2. `repository.reconcile_orphans()` — flips orphans to `SUPERSEDED` and records
   one `SOURCE_RETENTION_PRUNE` event per row, in the same transaction as the
   lifecycle flip.
3. `repository.verify_projection()` — the result is written to
   `meta.last_run_projection` / `last_run_projection_ok` / `last_run_orphans`.

The whole block is best-effort: a failure is logged and the scrape still
succeeds, because a projection hiccup must not cost a day's crawl.

The first repair run against production found 165 orphans and 102 opportunities
that had never been projected at all — confirming the drift was ongoing, not a
one-off migration artifact. Post-repair: listings 1065, opportunities 1230,
live opportunities 1065, orphans 0, documents 1061, integrity_check ok.

### CI enforcement

`.github/workflows/daily-scrape.yml` runs a `test` job (compileall + `pytest`)
that the `scrape` job `needs`, so a regression cannot publish a static mirror.
The scrape job additionally re-runs `verify_projection()` and fails the build
with `::error::projection invariant violated — refusing to publish` rather than
committing a drifting `public/data.json`.

## 5. Remaining phases (intended direction)

3. Ingestion Engine — SourceAdapter / DocumentFetcher / Parser / CandidateDetector.
4. Extraction Engine — deterministic-first, optional LLM (DeepSeek) strict schema.
5. Dedup / linking / versioning — canonical IDs, corrigendum linkage, change events.
6. Eligibility + personalization — structured requirements, evidence-first states.
7. New UX — Radar dashboard, action queue, detail view, changes, saved/applications.
8. Alerts — event-based (NEW/DEADLINE_CHANGED/…), idempotent.
9. Admin / observability — protected admin, source-health, review queue, metrics.
10. Performance / security / polish — load testing, security review, a11y, CI.

Done since the 2026-09 audit, and therefore no longer "remaining": CI now runs
the test suite and gates publishing (item 10), the opportunity projection is
kept in sync and orphan-free (part of item 5), and the in-process scrape slot is
atomic with graceful shutdown (part of item 10). Still open: items 3, 4, 6, 7,
8, 9, and the auth/observability half of item 10 — `/api/refresh` and
`/api/flag` remain unauthenticated (localhost-bound only), and `ADMIN_SECRET` /
`ADMIN_RATE_LIMIT` in `config.py` are still unused because no `/admin` route
consumes them.

## 6. Non-negotiable constraints (carried from the master directive)

- Never fabricate recruitment or eligibility data; every claim carries evidence.
- Incremental migration over rewrite; `listings` and legacy pipeline stay runnable.
- Recall > features; evidence-grounding > generic AI; data correctness > UI.
- Corrections (corrigendum/addendum/extension) link to opportunities, never dropped.
- Page disappearing ≠ closing; historical rows are superseded, not deleted.
