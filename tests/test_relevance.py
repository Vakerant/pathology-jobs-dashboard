"""Unit tests for scraper.relevance classification (no network)."""
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
