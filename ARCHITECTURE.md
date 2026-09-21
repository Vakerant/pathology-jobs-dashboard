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
| Service layer | `repository.py` | opportunity-first access + institution derivation |
| Tests | `tests/` | 52 tests across date/relevance/dedup/enrich/document_type/repository |

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
with rollback, and a `_verify` invariant (opportunities count == listings count;
0 ≤ documents ≤ listings).

Result on the production `data.db` (lossless): listings 1128, opportunities
1128 (identical key sets), documents 1061 (per-URL collapse), institutions 34,
source_status 34, meta 8 — schema_version 3, integrity_check ok.

## 5. Remaining phases (intended direction)

3. Ingestion Engine — SourceAdapter / DocumentFetcher / Parser / CandidateDetector.
4. Extraction Engine — deterministic-first, optional LLM (DeepSeek) strict schema.
5. Dedup / linking / versioning — canonical IDs, corrigendum linkage, change events.
6. Eligibility + personalization — structured requirements, evidence-first states.
7. New UX — Radar dashboard, action queue, detail view, changes, saved/applications.
8. Alerts — event-based (NEW/DEADLINE_CHANGED/…), idempotent.
9. Admin / observability — protected admin, source-health, review queue, metrics.
10. Performance / security / polish — load testing, security review, a11y, CI.

## 6. Non-negotiable constraints (carried from the master directive)

- Never fabricate recruitment or eligibility data; every claim carries evidence.
- Incremental migration over rewrite; `listings` and legacy pipeline stay runnable.
- Recall > features; evidence-grounding > generic AI; data correctness > UI.
- Corrections (corrigendum/addendum/extension) link to opportunities, never dropped.
- Page disappearing ≠ closing; historical rows are superseded, not deleted.
