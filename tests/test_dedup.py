"""Unit tests for db.py dedup: norm_title, strip_serial, make_key."""
from db import norm_title, strip_serial, make_key


class TestNormTitle:
    def test_strips_leading_serial(self):
        assert norm_title("1. Senior Resident Pathology") == "senior resident pathology"

    def test_normalizes_quotes_and_case(self):
        assert norm_title("Senior Resident \u201cPathology\u201d") == "senior resident \"pathology\""

    def test_collapses_whitespace(self):
        assert norm_title("  Senior   Resident   Pathology  ") == "senior resident pathology"


class TestStripSerial:
    def test_removes_serial_for_display(self):
        assert strip_serial("1. Senior Resident Pathology") == "Senior Resident Pathology"

    def test_no_serial_unchanged(self):
        assert strip_serial("Senior Resident Pathology") == "Senior Resident Pathology"


class TestMakeKey:
    def test_stable_same_input(self):
        a = make_key("s1", "http://x/y.pdf", "Senior Resident Pathology")
        b = make_key("s1", "http://x/y.pdf", "Senior Resident Pathology")
        assert a == b

    def test_diff_source_diff_key(self):
        a = make_key("s1", "http://x", "Senior Resident Pathology")
        b = make_key("s2", "http://x", "Senior Resident Pathology")
        assert a != b

    def test_short_title_uses_url_fallback(self):
        # title < 25 chars => ident uses url|title
        a = make_key("s1", "http://x/y.pdf", "Pathology")
        b = make_key("s1", "http://x/z.pdf", "Pathology")
        assert a != b  # different urls -> different keys
