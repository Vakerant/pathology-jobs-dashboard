"""Unit tests for scraper.relevance classification (no network)."""
import pytest

import scraper
from scraper import relevance


class TestRelevance:
    def test_pathology_high(self):
        assert relevance("Senior Resident Pathology vacancy at AIIMS") == "high"

    def test_histopathology_high(self):
        assert relevance("Walk-in for Histopathology Demonstrator") == "high"

    def test_transfusion_high(self):
        assert relevance("Blood Bank / Transfusion Medicine Senior Resident") == "high"

    def test_recruit_only_medium(self):
        assert relevance("Senior Resident vacancy General Surgery") == "medium"

    def test_negative_specialty_returns_none(self):
        # NEGATIVE specialties must be excluded
        assert relevance("Senior Resident Biochemistry") is None
        assert relevance("Radiology recruitment") is None
        assert relevance("Microbiology vacancy") is None

    def test_corrigendum_now_captured_for_linking(self):
        # Phase 5 fix: corrigendum/addendum are no longer dropped — they must be
        # captured and classified (via classify_document_type) so they can link
        # to their parent opportunity instead of being filtered out.
        assert relevance("Corrigendum: Pathology SR") == "high"
        assert relevance("Addendum: Fellowship") == "medium"

    def test_short_irrelevant_none(self):
        assert relevance("Home") is None

    def test_president_none(self):
        assert relevance("President notice pathology") is None


class TestVacancySignalGate:
    """'High' means pathology AND a vacancy signal, not just a pathology word."""

    @pytest.mark.parametrize("text", [
        "Blood Bank", "Pathology Services", "About Pathology", "Transfusion Medicine Event",
        "GEM tender for slide scanner by Department of Pathology",
        "Limited Tender enquiry for consumables Dept of Pathology",
    ])
    def test_department_pages_and_tenders_are_not_high(self, text):
        assert scraper.relevance(text) != "high"

    @pytest.mark.parametrize("text", [
        "Walk-in interview for Senior Resident Pathology",
        "Fellow - Clinical Haematology & Cellular Therapies",
        "Assistant Professor (Histopathology)",
        "Recruitment of Assistant Professor Pathology",
    ])
    def test_real_vacancies_stay_high(self, text):
        assert scraper.relevance(text) == "high"


def test_rescore_demotes_only_keyword_only_rows(tmp_path, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    base = dict(source_id="s", source_name="S", region="R", category="C", snippet="")
    db.upsert_listings([
        {**base, "title": "Blood Bank", "url": "https://x/blood-bank", "relevance": "high"},
        {**base, "title": "Walk-in interview Senior Resident Pathology", "url": "https://x/a", "relevance": "high"},
        {**base, "title": "Interview Notification", "url": "https://x/n.pdf", "relevance": "high"},
    ])
    assert scraper.rescore_stored() == 1
    got = {r["title"]: r["relevance"] for r in db.fetch_listings()}
    assert got["Blood Bank"] == "medium"
    assert got["Walk-in interview Senior Resident Pathology"] == "high"
    assert got["Interview Notification"] == "high"   # PDF-promoted: untouched
