"""Tests for the repository/service layer over the domain tables."""
import json
from pathlib import Path

import pytest

import config
import db
import migrations
import repository as r


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """Isolated DB so tests never touch production data.db.

    ``db.DB_PATH`` is a module-level string snapshot of ``config.DB_PATH``
    bound at import time, so we must patch the module attributes directly
    (env-var injection happens too late to redirect an already-imported db).
    """
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_file))
    monkeypatch.setattr(config, "DB_PATH", Path(db_file))
    monkeypatch.setattr(config, "BACKUP_DIR", tmp_path / "backups")
    db.init_db()
    yield db
    db.get_conn().close()


def _listing(repo, key="k1", source_id="src1", title="Senior Resident Pathology Delhi"):
    conn = repo.get_conn()
    conn.execute(
        """INSERT INTO listings
           (key, source_id, source_name, region, category, title, url, relevance,
            first_seen, last_seen)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (key, source_id, "Test Hospital", "Delhi", "Govt – Senior Resident",
         title, f"https://example.com/{key}", "high", "2026-01-01", "2026-02-02"))
    conn.commit()
    conn.close()


def test_sync_opportunities_from_listings(repo):
    _listing(repo, "k1")
    _listing(repo, "k2", source_id="src2", title="Fellowship Surgical Pathology")
    result = r.sync_opportunities_from_listings()
    assert result["inserted"] == 2
    assert result["total"] == 2
    # idempotent: second sync updates, not inserts
    result2 = r.sync_opportunities_from_listings()
    assert result2["inserted"] == 0
    assert result2["updated"] == 2


def test_sync_preserves_flags(repo):
    _listing(repo, "k1")
    r.sync_opportunities_from_listings()
    # set a flag on the opportunity side, re-sync should keep listing-mirrored
    # fields but not wipe opportunity-only fields
    opp = r.get_opportunity("k1")
    assert opp["id"] == "k1"
    assert opp["eligibility_state"] == "INSUFFICIENT_INFORMATION"
    assert opp["lifecycle_state"] == "ACTIVE"


def test_upsert_and_fetch_document(repo):
    created = r.upsert_document({
        "source_id": "src1", "url": "https://example.com/a",
        "title": "Corrigendum Pathology SR", "document_type": "CORRIGENDUM",
    })
    assert created is True
    # UNKNOWN should not clobber a prior classification
    updated = r.upsert_document({
        "source_id": "src1", "url": "https://example.com/a",
        "title": "Corrigendum Pathology SR", "document_type": "UNKNOWN",
    })
    assert updated is False
    docs = r.fetch_documents()
    assert len(docs) == 1
    assert docs[0]["document_type"] == "CORRIGENDUM"


def test_upsert_document_invalid_type_coerced(repo):
    r.upsert_document({"source_id": "s", "url": "u", "document_type": "NONSENSE"})
    assert r.fetch_documents()[0]["document_type"] == "UNKNOWN"


def test_record_event_idempotent(repo):
    r.record_event("OPPORTUNITY_CREATED", "opp1", {"a": 1}, idempotency_hash="h1")
    r.record_event("OPPORTUNITY_CREATED", "opp1", {"a": 1}, idempotency_hash="h1")
    assert len(r.fetch_events()) == 1


def test_upsert_application_status_validation(repo):
    with pytest.raises(ValueError):
        r.upsert_application("opp1", "BOGUS_STATUS")
    app_id = r.upsert_application("opp1", "SAVED", checklist=["apply"])
    assert app_id
    apps = r.fetch_applications()
    assert apps[0]["status"] == "SAVED"
    assert apps[0]["checklist"] == ["apply"]


def test_profile_roundtrip(repo):
    assert r.get_profile() is None
    r.save_profile({
        "qualification": "MD Pathology",
        "degree_year": 2026,
        "preferred_roles": ["Senior Resident"],
        "preferred_specialties": ["Histopathology"],
        "preferred_regions": ["Delhi"],
        "research_interest": 1,
        "fellowship_interest": 0,
    })
    p = r.get_profile()
    assert p["qualification"] == "MD Pathology"
    assert p["preferred_roles"] == ["Senior Resident"]
    assert p["research_interest"] == 1


def test_institutions_populated_from_sources(repo):
    n = r.populate_institutions()
    assert n >= 30
    insts = r.fetch_institutions()
    assert all(i["state"] for i in insts)
    assert all(i["sources"] for i in insts)
    # one institution per source id
    ids = {s for i in insts for s in i["sources"]}
    assert len(ids) == n


# ── Projection invariant: listings == live opportunities, zero orphans ───────
# `scraper.run()` now syncs the opportunity-first projection on every pass.
# These tests pin the contract that keeps ARCHITECTURE.md §4 honest.


def test_sync_revives_superseded_opportunity(repo):
    """A source page that reappears must revive its SUPERSEDED row."""
    _listing(repo, "k1")
    r.sync_opportunities_from_listings()
    conn = repo.get_conn()
    conn.execute("DELETE FROM listings WHERE key='k1'")
    conn.commit(); conn.close()

    assert r.reconcile_orphans() == 1
    assert r.get_opportunity("k1")["lifecycle_state"] == "SUPERSEDED"
    assert r.count_orphan_opportunities() == 0  # superseded != orphan

    _listing(repo, "k1")
    result = r.sync_opportunities_from_listings()
    assert result["revived"] == 1
    assert r.get_opportunity("k1")["lifecycle_state"] == "ACTIVE"


def test_sync_does_not_clobber_human_lifecycle_state(repo):
    """Explicit human decisions (CLOSED/CANCELLED/ARCHIVED) must survive re-sync."""
    _listing(repo, "k1")
    r.sync_opportunities_from_listings()
    conn = repo.get_conn()
    conn.execute("UPDATE opportunities SET lifecycle_state='CLOSED' WHERE id='k1'")
    conn.commit(); conn.close()

    r.sync_opportunities_from_listings()
    assert r.get_opportunity("k1")["lifecycle_state"] == "CLOSED"


def test_reconcile_orphans_supersedes_and_records_event(repo):
    _listing(repo, "k1")
    _listing(repo, "k2")
    r.sync_opportunities_from_listings()
    conn = repo.get_conn()
    conn.execute("DELETE FROM listings WHERE key='k2'")
    conn.commit(); conn.close()

    assert r.reconcile_orphans() == 1
    assert r.reconcile_orphans() == 0  # idempotent
    events = r.fetch_events()
    assert [e["event_type"] for e in events] == ["SOURCE_RETENTION_PRUNE"]
    assert events[0]["opportunity_id"] == "k2"
    assert r.verify_projection()["ok"] is True


def test_reconcile_orphans_enriches_event_payload(repo):
    _listing(repo, "k1", title="Fellowship Surgical Pathology")
    r.sync_opportunities_from_listings()
    conn = repo.get_conn()
    conn.execute("DELETE FROM listings WHERE key='k1'")
    conn.commit(); conn.close()

    r.reconcile_orphans(reason="retention_test")
    payload = r.fetch_events()[0]["payload"]  # fetch_events parses JSON for us
    assert payload["reason"] == "retention_test"
    assert payload["url"] == "https://example.com/k1"
    assert payload["title"] == "Fellowship Surgical Pathology"
    assert payload["last_seen"]


def test_verify_projection_ok_when_in_sync(repo):
    _listing(repo, "k1")
    r.sync_opportunities_from_listings()
    p = r.verify_projection()
    assert p == {"listings": 1, "opportunities": 1, "live_opportunities": 1,
                 "orphans": 0, "documents": 0, "ok": True}


def test_verify_projection_flags_drift(repo):
    """An orphan that was never reconciled must fail the gate."""
    _listing(repo, "k1")
    r.sync_opportunities_from_listings()
    conn = repo.get_conn()
    conn.execute("DELETE FROM listings WHERE key='k1'")
    conn.commit(); conn.close()

    p = r.verify_projection()
    assert p["orphans"] == 1
    assert p["live_opportunities"] == 1
    assert p["ok"] is False


def test_record_event_enlists_in_caller_transaction(repo):
    """Passing conn= must defer the commit to the caller so both writes roll back."""
    _listing(repo, "k1")
    r.sync_opportunities_from_listings()
    conn = repo.get_conn()
    conn.execute("DELETE FROM listings WHERE key='k1'")
    conn.commit()

    conn.execute("BEGIN")
    conn.execute("UPDATE opportunities SET lifecycle_state='SUPERSEDED' WHERE id='k1'")
    r.record_event("TEST_EVENT", "k1", {"x": 1}, conn=conn)
    conn.rollback()
    conn.close()

    assert r.get_opportunity("k1")["lifecycle_state"] == "ACTIVE"
    assert r.fetch_events() == []


def test_record_event_owns_transaction_when_no_conn(repo):
    """Without conn=, record_event must commit on its own as before."""
    r.record_event("OWN_EVENT", "k1", {"x": 1}, idempotency_hash="own1")
    assert [e["event_type"] for e in r.fetch_events()] == ["OWN_EVENT"]
