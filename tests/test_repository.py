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
           (key, source_id, source_name, region, category, title, url, relevance)
           VALUES (?,?,?,?,?,?,?,?)""",
        (key, source_id, "Test Hospital", "Delhi", "Govt – Senior Resident",
         title, f"https://example.com/{key}", "high"))
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
