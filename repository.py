"""Repository / service layer over the domain tables.

This module is the single access point for the opportunity-first domain model
introduced in migration #2 (schema_version 2): opportunities, documents,
document_versions, events, institutions, scrape_runs, source_runs,
user_profile, and applications.

It does NOT replace ``db`` — ``db`` remains the owner of the legacy
``listings`` table and the scrape/flag plumbing. This layer sits on top and
exposes the new tables with:

  * JSON columns serialized at the boundary (json.dumps / json.loads),
  * deterministic IDs (same hashing used by migrations),
  * a lossless mirror helper that keeps ``opportunities`` in sync with
    ``listings`` (so new scrapes flow into the domain model without loss),
  * institution derivation from the factual ``sources.SOURCES`` registry.

All writes use bound parameters (SQLi-safe). No recruitment data is invented;
fields that are unknown stay NULL / empty rather than guessed.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

import db
from sources import SOURCES

# document_type values mirror migrations.documents.document_type.
DOCUMENT_TYPES = {
    "ADVERTISEMENT", "CORRIGENDUM", "ADDENDUM", "EXTENSION",
    "INTERVIEW_NOTICE", "SHORTLIST", "RESULT", "CANCELLATION",
    "RECRUITMENT_RULE", "UNKNOWN",
}

APPLICATION_STATUSES = {
    "SAVED", "REVIEWING", "READY_TO_APPLY", "APPLIED", "INTERVIEW",
    "SELECTED", "JOINED", "DECLINED", "EXPIRED", "NOT_ELIGIBLE",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _doc_id(source_id: str | None, url: str | None) -> str:
    """Deterministic document id (must match migrations._doc_id)."""
    return hashlib.sha1(f"{source_id or ''}|{url or ''}".encode("utf-8", "ignore")).hexdigest()


def _jload(value, default):
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _jdump(value):
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Institutions
# ---------------------------------------------------------------------------

def _clean_institution_name(name: str) -> str:
    """Strip trailing parentheticals like '(Recruitment)', '(Vacancies)', '(Jobs)'."""
    return re.sub(r"\s*\((?:recruitment|vacancies|vacancy|jobs|careers|exams|portal)\)\s*$",
                  "", (name or "").strip(), flags=re.IGNORECASE).strip()


def _state_from_region(region: str) -> str | None:
    """Best-effort factual state extraction; returns None when ambiguous."""
    r = (region or "").strip()
    known = {
        "delhi", "chandigarh", "punjab", "haryana", "himachal pradesh",
        "mumbai", "gujarat", "ahmedabad", "west bengal", "kolkata",
        "karnataka", "puducherry", "madhya pradesh", "rajasthan",
        "uttarakhand", "odisha", "private", "metro", "fellowship",
    }
    # strip "/ INI" style suffixes
    first = r.split("/")[0].strip()
    if first.lower() in known:
        return first
    # region like "Ahmedabad / Gujarat" -> take Gujarat
    parts = [p.strip() for p in r.replace("/", ",").split(",")]
    for p in parts:
        if p.lower() in known:
            return p
    return None


# Well-established city -> state mapping (factual geography, not recruitment
# data). Used only when the city is explicitly present in the institution name.
_CITY_STATE = {
    "bhubaneswar": "Odisha",
    "jodhpur": "Rajasthan",
    "rishikesh": "Uttarakhand",
    "bhopal": "Madhya Pradesh",
    "bilaspur": "Himachal Pradesh",
    "puducherry": "Puducherry",
    "bengaluru": "Karnataka",
    "bangalore": "Karnataka",
    "kolkata": "West Bengal",
    "mumbai": "Maharashtra",
    "shimla": "Himachal Pradesh",
    "chamiana": "Himachal Pradesh",
    "karnal": "Haryana",
    "patiala": "Punjab",
    "rohtak": "Haryana",
    "faridkot": "Punjab",
    "ahmedabad": "Gujarat",
    "new delhi": "Delhi",
    "delhi": "Delhi",
    "chandigarh": "Chandigarh",
}


def _state_from_name(name: str) -> str | None:
    """Extract a state from an explicit city token in the institution name."""
    n = (name or "").lower()
    for city, state in _CITY_STATE.items():
        if city in n:
            return state
    return None


def _reliability_class(category: str) -> str | None:
    """Factual classification from the category string (no fabrication)."""
    c = (category or "").lower()
    if "private" in c:
        return "PRIVATE"
    if "ini" in c:
        return "INI"
    if "fellowship" in c or "drnb" in c or "fnb" in c:
        return "FELLOWSHIP"
    if "govt" in c or "research" in c or "cancer centre" in c:
        return "GOVT"
    return None


def _institution_id_from_source(src: dict) -> str:
    """Stable institution id keyed on the source (1 source -> 1 institution row)."""
    return hashlib.sha1(f"inst|{src['id']}".encode("utf-8")).hexdigest()


def populate_institutions() -> int:
    """Derive institution rows from the factual SOURCES registry.

    Conservative: one institution row per source. Grouping multiple sources
    under a shared institution (e.g. all AIIMS portals) is deliberately left
    for a later refinement to avoid incorrectly merging distinct entities.
    Returns the number of institutions present after the call.
    """
    conn = db.get_conn()
    try:
        for src in SOURCES:
            inst_id = _institution_id_from_source(src)
            name = _clean_institution_name(src["name"])
            conn.execute(
                """INSERT INTO institutions
                   (institution_id, name, state, departments, locations,
                    sources, reliability, created_at)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(institution_id) DO UPDATE SET
                     name=excluded.name,
                     state=COALESCE(excluded.state, institutions.state),
                     sources=excluded.sources,
                     reliability=excluded.reliability""",
                (inst_id, name,
                 _state_from_region(src["region"]) or _state_from_name(name),
                 _jdump([]),
                 _jdump([]),
                 _jdump([src["id"]]),
                 _reliability_class(src["category"]),
                 _now()),
            )
        conn.commit()
        return conn.execute("SELECT COUNT(*) FROM institutions").fetchone()[0]
    finally:
        conn.close()


def fetch_institutions() -> list[dict]:
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT * FROM institutions ORDER BY name").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["departments"] = _jload(d.get("departments"), [])
            d["locations"] = _jload(d.get("locations"), [])
            d["sources"] = _jload(d.get("sources"), [])
            out.append(d)
        return out
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Opportunities <-> listings mirror
# ---------------------------------------------------------------------------

def sync_opportunities_from_listings() -> dict:
    """Losslessly mirror listings -> opportunities.

    * New listing keys are INSERT-ed as opportunities (id == listing key).
    * Existing opportunities have their listing-mirrored fields refreshed.
    * No opportunity row is deleted here (historical data is never dropped
      by a mirror; removal is an explicit lifecycle transition).
    Returns {inserted, updated, total}.
    """
    conn = db.get_conn()
    try:
        listings = conn.execute("SELECT * FROM listings").fetchall()
        inserted = 0
        updated = 0
        now = _now()
        for l in listings:
            d = dict(l)
            key = d["key"]
            exists = conn.execute(
                "SELECT 1 FROM opportunities WHERE id=?", (key,)).fetchone()
            if exists:
                conn.execute(
                    """UPDATE opportunities SET
                         listing_key=?, source_id=?, source_name=?, region=?,
                         category=?, relevance=?, title=?, url=?, snippet=?,
                         notice_date=?, deadline=?, is_seed=?, starred=?,
                         hidden=?, first_seen=?, last_seen=?, last_verified_at=?
                       WHERE id=?""",
                    (key, d["source_id"], d["source_name"], d["region"],
                     d["category"], d["relevance"], d["title"], d["url"],
                     d.get("snippet", ""), d.get("notice_date"), d.get("deadline"),
                     int(d.get("is_seed", 0)), int(d.get("starred", 0)),
                     int(d.get("hidden", 0)), d.get("first_seen"), d.get("last_seen"),
                     now, key),
                )
                updated += 1
            else:
                conn.execute(
                    """INSERT INTO opportunities
                       (id, listing_key, source_id, source_name, region,
                        category, relevance, title, url, snippet,
                        notice_date, deadline, is_seed, starred, hidden,
                        first_seen, last_seen, discovered_at, last_verified_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (key, key, d["source_id"], d["source_name"], d["region"],
                     d["category"], d["relevance"], d["title"], d["url"],
                     d.get("snippet", ""), d.get("notice_date"), d.get("deadline"),
                     int(d.get("is_seed", 0)), int(d.get("starred", 0)),
                     int(d.get("hidden", 0)), d.get("first_seen"), d.get("last_seen"),
                     d.get("first_seen") or now, now),
                )
                inserted += 1
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0]
        return {"inserted": inserted, "updated": updated, "total": total}
    finally:
        conn.close()


def fetch_opportunities() -> list[dict]:
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT * FROM opportunities ORDER BY first_seen DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for col in ("specialty", "sub_specialty", "qualification",
                        "documents_required", "evidence"):
                d[col] = _jload(d.get(col), None)
            out.append(d)
        return out
    finally:
        conn.close()


def get_opportunity(opportunity_id: str) -> dict | None:
    conn = db.get_conn()
    try:
        r = conn.execute("SELECT * FROM opportunities WHERE id=?",
                         (opportunity_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        for col in ("specialty", "sub_specialty", "qualification",
                    "documents_required", "evidence"):
            d[col] = _jload(d.get(col), None)
        return d
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

def upsert_document(doc: dict) -> bool:
    """Insert or update a document row (document_type UNKNOWN never clobbers)."""
    doc_id = doc.get("document_id") or _doc_id(doc.get("source_id"), doc.get("url"))
    conn = db.get_conn()
    try:
        exists = conn.execute("SELECT 1 FROM documents WHERE document_id=?",
                              (doc_id,)).fetchone()
        dt = doc.get("document_type", "UNKNOWN")
        if dt not in DOCUMENT_TYPES:
            dt = "UNKNOWN"
        if exists:
            conn.execute(
                """UPDATE documents SET
                     url=COALESCE(?, url), canonical_url=COALESCE(?, canonical_url),
                     content_type=COALESCE(?, content_type), title=COALESCE(?, title),
                     published_at=COALESCE(?, published_at),
                     content_hash=COALESCE(?, content_hash),
                     raw_text=COALESCE(?, raw_text),
                     extracted_text=COALESCE(?, extracted_text),
                     parser=COALESCE(?, parser), parser_version=COALESCE(?, parser_version),
                     http_status=COALESCE(?, http_status), freshness=COALESCE(?, freshness),
                     document_type=CASE WHEN ?='UNKNOWN' THEN document_type ELSE ? END,
                     opportunity_id=COALESCE(?, opportunity_id),
                     retrieved_at=?
                   WHERE document_id=?""",
                (doc.get("url"), doc.get("canonical_url"), doc.get("content_type"),
                 doc.get("title"), doc.get("published_at"), doc.get("content_hash"),
                 doc.get("raw_text"), doc.get("extracted_text"), doc.get("parser"),
                 doc.get("parser_version"), doc.get("http_status"), doc.get("freshness"),
                 dt, dt, doc.get("opportunity_id"), _now(), doc_id),
            )
        else:
            conn.execute(
                """INSERT INTO documents
                   (document_id, source_id, url, canonical_url, content_type,
                    title, retrieved_at, published_at, content_hash, raw_text,
                    extracted_text, parser, parser_version, http_status,
                    freshness, document_type, opportunity_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (doc_id, doc.get("source_id"), doc.get("url"),
                 doc.get("canonical_url"), doc.get("content_type"), doc.get("title"),
                 _now(), doc.get("published_at"), doc.get("content_hash"),
                 doc.get("raw_text"), doc.get("extracted_text"), doc.get("parser"),
                 doc.get("parser_version"), doc.get("http_status"), doc.get("freshness"),
                 dt, doc.get("opportunity_id")),
            )
        conn.commit()
        return not exists
    finally:
        conn.close()


def fetch_documents(opportunity_id: str | None = None) -> list[dict]:
    conn = db.get_conn()
    try:
        if opportunity_id:
            rows = conn.execute(
                "SELECT * FROM documents WHERE opportunity_id=? ORDER BY retrieved_at",
                (opportunity_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM documents ORDER BY retrieved_at").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def record_event(event_type: str, opportunity_id: str | None,
                 payload: dict | None = None, idempotency_hash: str | None = None) -> str:
    """Insert an event; idempotent on idempotency_hash when provided."""
    payload = payload or {}
    idem = idempotency_hash or hashlib.sha1(
        f"{event_type}|{opportunity_id}|{_jdump(payload)}".encode("utf-8")).hexdigest()
    event_id = hashlib.sha1(
        f"{idem}|{_now()}".encode("utf-8")).hexdigest()
    conn = db.get_conn()
    try:
        if idempotency_hash:
            exists = conn.execute(
                "SELECT 1 FROM events WHERE idempotency_hash=?", (idem,)).fetchone()
            if exists:
                return event_id
        conn.execute(
            """INSERT OR IGNORE INTO events
               (event_id, event_type, opportunity_id, occurred_at, payload, idempotency_hash)
               VALUES (?,?,?,?,?,?)""",
            (event_id, event_type, opportunity_id, _now(), _jdump(payload), idem))
        conn.commit()
        return event_id
    finally:
        conn.close()


def fetch_events(opportunity_id: str | None = None) -> list[dict]:
    conn = db.get_conn()
    try:
        if opportunity_id:
            rows = conn.execute(
                "SELECT * FROM events WHERE opportunity_id=? ORDER BY occurred_at",
                (opportunity_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM events ORDER BY occurred_at DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = _jload(d.get("payload"), {})
            out.append(d)
        return out
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

def upsert_application(opportunity_id: str, status: str,
                       checklist: list | None = None, notes: str | None = None) -> str:
    if status not in APPLICATION_STATUSES:
        raise ValueError(f"invalid application status: {status}")
    app_id = hashlib.sha1(f"app|{opportunity_id}".encode("utf-8")).hexdigest()
    conn = db.get_conn()
    try:
        conn.execute(
            """INSERT INTO applications
               (id, opportunity_id, status, checklist, notes, applied_at, updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 status=excluded.status,
                 checklist=excluded.checklist,
                 notes=excluded.notes,
                 updated_at=excluded.updated_at""",
            (app_id, opportunity_id, status, _jdump(checklist or []), notes,
             _now() if status == "APPLIED" else None, _now()))
        conn.commit()
        return app_id
    finally:
        conn.close()


def fetch_applications() -> list[dict]:
    conn = db.get_conn()
    try:
        rows = conn.execute("SELECT * FROM applications ORDER BY updated_at DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["checklist"] = _jload(d.get("checklist"), [])
            out.append(d)
        return out
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# User profile (singleton)
# ---------------------------------------------------------------------------

def get_profile() -> dict | None:
    conn = db.get_conn()
    try:
        r = conn.execute("SELECT * FROM user_profile WHERE id=1").fetchone()
        if not r:
            return None
        d = dict(r)
        for col in ("preferred_roles", "preferred_specialties", "preferred_regions",
                    "excluded_roles", "notification_preferences"):
            d[col] = _jload(d.get(col), [])
        return d
    finally:
        conn.close()


def save_profile(profile: dict) -> None:
    """Save the singleton user profile (id=1). Never hardcodes the author."""
    conn = db.get_conn()
    try:
        conn.execute(
            """INSERT INTO user_profile
               (id, qualification, degree_year, registration, preferred_roles,
                preferred_specialties, preferred_regions, work_mode,
                gov_private_pref, research_interest, fellowship_interest,
                excluded_roles, notification_preferences, last_seen_at)
               VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 qualification=excluded.qualification,
                 degree_year=excluded.degree_year,
                 registration=excluded.registration,
                 preferred_roles=excluded.preferred_roles,
                 preferred_specialties=excluded.preferred_specialties,
                 preferred_regions=excluded.preferred_regions,
                 work_mode=excluded.work_mode,
                 gov_private_pref=excluded.gov_private_pref,
                 research_interest=excluded.research_interest,
                 fellowship_interest=excluded.fellowship_interest,
                 excluded_roles=excluded.excluded_roles,
                 notification_preferences=excluded.notification_preferences,
                 last_seen_at=excluded.last_seen_at""",
            (profile.get("qualification"), profile.get("degree_year"),
             profile.get("registration"), _jdump(profile.get("preferred_roles", [])),
             _jdump(profile.get("preferred_specialties", [])),
             _jdump(profile.get("preferred_regions", [])),
             profile.get("work_mode"), profile.get("gov_private_pref"),
             int(profile.get("research_interest", 0)),
             int(profile.get("fellowship_interest", 0)),
             _jdump(profile.get("excluded_roles", [])),
             _jdump(profile.get("notification_preferences", [])),
             _now()))
        conn.commit()
    finally:
        conn.close()
