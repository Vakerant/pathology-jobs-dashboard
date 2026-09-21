"""Unit tests for db.enrich live flags (expired / closing_soon / is_new)."""
import datetime
from db import enrich


def _row(**kw):
    base = {
        "is_seed": 0, "notice_date": None, "deadline": None,
        "first_seen": None,
    }
    base.update(kw)
    return base


class TestEnrich:
    def test_seed_never_expired(self):
        r = enrich([_row(is_seed=1, deadline="2000-01-01")])
        assert r[0]["expired"] is False

    def test_past_deadline_expired(self):
        r = enrich([_row(deadline="2020-01-01")])
        assert r[0]["expired"] is True

    def test_future_deadline_not_expired(self):
        future = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        r = enrich([_row(deadline=future)])
        assert r[0]["expired"] is False

    def test_closing_soon_within_7d(self):
        soon = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
        r = enrich([_row(deadline=soon)])
        assert r[0]["closing_soon"] is True

    def test_closing_soon_outside_7d(self):
        later = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
        r = enrich([_row(deadline=later)])
        assert r[0]["closing_soon"] is False

    def test_is_new_recent_notice(self):
        recent = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
        r = enrich([_row(notice_date=recent)])
        assert r[0]["is_new"] is True

    def test_is_new_recent_first_seen(self):
        recent = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()
        r = enrich([_row(first_seen=recent)])
        assert r[0]["is_new"] is True
