"""Unit tests for scraper.classify_document_type (no network)."""
from scraper import classify_document_type


class TestDocumentType:
    def test_cancellation(self):
        assert classify_document_type("Recruitment cancelled for Pathology SR") == "CANCELLATION"

    def test_extension(self):
        assert classify_document_type("Extension of last date for application") == "EXTENSION"
        assert classify_document_type("Walk-in postponed") == "EXTENSION"

    def test_corrigendum(self):
        assert classify_document_type("Corrigendum: Pathology SR AIIMS") == "CORRIGENDUM"
        assert classify_document_type("Amendment to fellowship notice") == "CORRIGENDUM"

    def test_addendum(self):
        assert classify_document_type("Addendum to recruitment notice") == "ADDENDUM"
        assert classify_document_type("Clarification regarding eligibility") == "ADDENDUM"

    def test_interview_notice(self):
        assert classify_document_type("Interview notice for Senior Resident") == "INTERVIEW_NOTICE"
        assert classify_document_type("Walk-in interview Pathology") == "INTERVIEW_NOTICE"

    def test_shortlist(self):
        assert classify_document_type("Shortlist of eligible candidates") == "SHORTLIST"

    def test_result(self):
        assert classify_document_type("Result of Pathology SR exam") == "RESULT"
        assert classify_document_type("Final merit list announced") == "RESULT"

    def test_recruitment_rule(self):
        assert classify_document_type("Recruitment rules for SR posts") == "RECRUITMENT_RULE"

    def test_advertisement(self):
        assert classify_document_type("Applications are invited for Pathology SR") == "ADVERTISEMENT"
        assert classify_document_type("Recruitment of Senior Resident") == "ADVERTISEMENT"

    def test_unknown(self):
        assert classify_document_type("") == "UNKNOWN"
        assert classify_document_type("Some unrelated text") == "UNKNOWN"

    def test_precedence_corrigendum_over_advertisement(self):
        # corrigendum should win over a generic 'recruitment' signal
        assert classify_document_type("Corrigendum to recruitment of Pathology SR") == "CORRIGENDUM"
