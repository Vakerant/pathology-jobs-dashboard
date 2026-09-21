"""Unit tests for scraper date extraction (deterministic, no network)."""
import pytest
from scraper import extract_dates, extract_latest_date


class TestExtractDates:
    def test_dmy_deadline(self):
        text = "Applications close on 18/09/2026. Apply now."
        notice, deadline = extract_dates(text)
        assert deadline == "2026-09-18"

    def test_dmy_notice_only(self):
        text = "Advertisement dated 15/09/2026."
        notice, deadline = extract_dates(text)
        assert notice == "2026-09-15"
        assert deadline is None

    def test_dmy_month_name(self):
        text = "Last date: 20 September 2026"
        notice, deadline = extract_dates(text)
        assert deadline == "2026-09-20"

    def test_ymd_deadline(self):
        text = "Walk-in interview 2026/10/05"
        notice, deadline = extract_dates(text)
        assert deadline is not None  # walk-in labelled as deadline
        assert deadline == "2026-10-05"

    def test_walk_in_labelled_deadline(self):
        text = "Walk-in interview on 05/10/2026 at 9 AM"
        notice, deadline = extract_dates(text)
        assert deadline == "2026-10-05"

    def test_extended_date_is_deadline(self):
        text = "Extended upto 30/09/2026"
        notice, deadline = extract_dates(text)
        assert deadline == "2026-09-30"

    def test_out_of_range_year_rejected(self):
        text = "Last date 18/09/2019 (old)"
        notice, deadline = extract_dates(text)
        assert deadline is None
        assert notice is None

    def test_no_dates(self):
        assert extract_dates("No dates here") == (None, None)

    def test_latest_date_max(self):
        text = "Notice 10/09/2026. Deadline 25/09/2026"
        assert extract_latest_date(text) == "2026-09-25"

    def test_invalid_day_month_rejected(self):
        text = "Last date 32/13/2026"
        notice, deadline = extract_dates(text)
        assert deadline is None
