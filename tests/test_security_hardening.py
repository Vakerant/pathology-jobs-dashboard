"""Regressions for the abuse-protection and scope-backstop fixes.

Two defects are pinned here:

1. ``config.ADMIN_RATE_LIMIT`` was defined and documented as a control but read
   nowhere -- there was no ``before_request`` hook at all, so ``POST /api/refresh``
   (which fans out to every configured source) was unauthenticated *and*
   unthrottled.

2. ``relevance()`` judges a link's own text, but the title persisted to the DB
   can come from a page heading instead. A generic "Download" link on a page
   headed "Senior Residents (Dental)" therefore passed the link-level gate and
   stored a dental notice. ``scrape_source`` now re-checks the title it is about
   to write.
"""
import os

os.environ.setdefault("AUTO_SCRAPE_ENABLED", "0")

import pytest

import app as app_module
import config
import db
import scraper


@pytest.fixture(autouse=True)
def _clear_rate_buckets():
    """Rate-limit state is module-global, so it must not leak between tests."""
    app_module._rate_limit_reset()
    yield
    app_module._rate_limit_reset()


@pytest.fixture(autouse=True)
def _no_real_scrape(monkeypatch):
    """Keep the rate-limit tests hermetic.

    POST /api/refresh calls _start_scrape(), which really does fan out to all
    45 sources. Exercising the throttle without stubbing that meant every test
    request launched a live network scrape (observable as the
    "TLS verification disabled for ..." lines during the run) -- non-hermetic
    and the same defect class as F2. Throttling is a property of the endpoint,
    not of the scrape, so the scrape is stubbed here.
    """
    monkeypatch.setattr(app_module, "_start_scrape", lambda: True)


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# F11 -- the rate limiter that ADMIN_RATE_LIMIT always implied
# ---------------------------------------------------------------------------

def test_refresh_endpoint_is_throttled(client):
    """POST /api/refresh must stop answering once the per-IP budget is spent."""
    limit = config.REFRESH_RATE_LIMIT
    for i in range(limit):
        resp = client.post("/api/refresh")
        assert resp.status_code in (200, 202), f"request {i} should pass, got {resp.status_code}"

    blocked = client.post("/api/refresh")
    assert blocked.status_code == 429
    body = blocked.get_json()
    assert body["ok"] is False
    assert body["error"] == "rate limit exceeded"
    assert body["limit"] == limit
    # A client needs to know when it may try again.
    assert int(blocked.headers["Retry-After"]) >= 1


def test_flag_endpoint_is_throttled(client):
    limit = config.ADMIN_RATE_LIMIT
    payload = {"key": "0" * 40, "field": "starred", "value": 1}
    for _ in range(limit):
        # Status is irrelevant here; even a 400/404 still consumed budget.
        client.post("/api/flag", json=payload)
    assert client.post("/api/flag", json=payload).status_code == 429


def test_budget_is_per_client_ip(client):
    """One noisy client must not exhaust everyone else's allowance."""
    for _ in range(config.REFRESH_RATE_LIMIT):
        client.post("/api/refresh", environ_overrides={"REMOTE_ADDR": "10.0.0.1"})
    assert client.post(
        "/api/refresh", environ_overrides={"REMOTE_ADDR": "10.0.0.1"}
    ).status_code == 429

    fresh = client.post("/api/refresh", environ_overrides={"REMOTE_ADDR": "10.0.0.2"})
    assert fresh.status_code in (200, 202)


def test_read_only_dashboard_is_never_throttled(client):
    """The dashboard polls / and /api/data; a 429 there would break the product."""
    for _ in range(config.REFRESH_RATE_LIMIT + 3):
        assert client.get("/").status_code == 200
        assert client.get("/api/data").status_code == 200


def test_refresh_is_budgeted_tighter_than_flag():
    """A refresh is ~45 network scrapes; a flag is one row write."""
    assert config.REFRESH_RATE_LIMIT < config.ADMIN_RATE_LIMIT


def test_rate_limit_bucket_memory_is_bounded(client):
    """A rotating-IP flood must not grow the bucket dict without limit."""
    app_module._RATE_MAX_BUCKETS = 8
    for i in range(200):
        client.post(
            "/api/flag",
            json={"key": "0" * 40, "field": "starred", "value": 1},
            environ_overrides={"REMOTE_ADDR": f"172.16.0.{i % 250}"},
        )
    assert len(app_module._RATE_BUCKETS) > 1, "IPs must actually differ, else this is vacuous"
    assert len(app_module._RATE_BUCKETS) <= app_module._RATE_MAX_BUCKETS


def test_rate_limit_releases_a_quiet_ip(client):
    """A client that stops calling must not stay resident in the bucket dict.

    Regression test for a real defect: the prune step used to delete only
    *empty* buckets, but a bucket is only emptied by the sliding-window trim,
    which runs for the REQUESTING ip alone. So a quiet client's bucket was
    never released and the prune step was unreachable dead code -- memory was
    bounded only by the hard _RATE_MAX_BUCKETS cap, well after 4096 clients had
    accumulated. The sweep must key off each bucket's own oldest timestamp.
    """
    app_module._rate_limit_reset()
    flag = {"key": "0" * 40, "field": "starred", "value": 1}
    # Two clients each make one request, then both go silent.
    for ip in ("10.0.0.1", "10.0.0.2"):
        client.post("/api/flag", json=flag, environ_overrides={"REMOTE_ADDR": ip})
    assert set(app_module._RATE_BUCKETS) == {"10.0.0.1", "10.0.0.2"}

    # Rewind the recorded timestamps past the window without touching the window
    # length, so those two buckets are genuinely stale.
    for ip in ("10.0.0.1", "10.0.0.2"):
        for i in range(len(app_module._RATE_BUCKETS[ip])):
            app_module._RATE_BUCKETS[ip][i] -= app_module._RATE_WINDOW_SECONDS + 1

    # One new client arrives; its request should sweep the two dead ones.
    client.post("/api/flag", json=flag, environ_overrides={"REMOTE_ADDR": "10.0.0.3"})

    assert "10.0.0.1" not in app_module._RATE_BUCKETS
    assert "10.0.0.2" not in app_module._RATE_BUCKETS
    # The live client is kept -- sweeping must not evict the requester.
    assert "10.0.0.3" in app_module._RATE_BUCKETS
    app_module._rate_limit_reset()


# ---------------------------------------------------------------------------
# F12 -- final scope backstop on the persisted title
# ---------------------------------------------------------------------------
# The first three are the real titles that leaked through on the 2026-09-30
# nightly run (captured verbatim from the listings table, not reconstructed),
# which is why they are truncated nowhere and keep their trailing junk.

OFF_SCOPE_TITLES = [
    "Tentative Seat Matrix for Round-2 of counselling for admissions to MDS "
    "courses in Govt. and Private Dental Colleges in the State of Haryana, "
    "Including those underPrivate University (SGT University,Budhera Gurugram ) "
    "as on 29.09.2026, 03:00",
    "DEAN (ACADEMICS)/JR/45/2025-AIIMS.JDH Recruitment to the post of "
    "Non-Academic Junior Resident (Dental) at AIIMS Jodhpur (Rajasthan) View "
    "Document 11-02-2025 03-03-2025 Closed Online Result Notice for Exam and "
    "Interview Eligibility Status",
    "Interview notice for Senior Residents(Dental) Non-teaching(adhoc) in "
    "AAAGH. Download",
    "Recruitment for MDS (Oral & Maxillofacial Surgery) at Govt Dental College",
    "Walk-in interview for the post of Veterinary Officer",
]


@pytest.mark.parametrize("title", OFF_SCOPE_TITLES)
def test_off_scope_titles_are_rejected_by_the_title_gate(title):
    assert scraper._off_scope(title) is True


IN_SCOPE_TITLES = [
    "Senior Resident (Pathology) at AIIMS Bhopal",
    "Junior Resident, Department of Pathology, PGIMER Chandigarh",
    "Fellowship in Neuropathology -- NIMHANS Bengaluru",
    "Consultant (Pathology) walk-in interview, AIIMS Jodhpur",
    "Histopathology fellowship at ACTREC Tata Memorial Centre",
    # Mentions MDS but is genuinely an MD/MS teaching circular, so the narrow
    # blocklist must NOT reject it -- a broad "must mention pathology" rule
    # would have thrown this away.
    "CIRCULAR - Nomination of Resource faculty for the First Year Postgraduate "
    "Orientation Programme for MD/MS, DM/MCh & MDS students",
]


@pytest.mark.parametrize("title", IN_SCOPE_TITLES)
def test_genuine_pathology_titles_survive_the_title_gate(title):
    assert scraper._off_scope(title) is False


def test_scrape_source_filters_items_by_the_title_gate(monkeypatch, tmp_path):
    """The persist loop must drop an off-scope item and keep the MD one.

    Re-implements scrape_source's loop body (including the new backstop) so the
    assertion is about which rows reach the DB, not about the helper alone.
    """
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "backstop.db"))
    db.init_db()

    items = [
        {"title": "Interview notice for Senior Residents(Dental) Non-teaching(adhoc)",
         "url": "https://example.invalid/0", "rel": "high"},
        {"title": "Senior Resident (Pathology) at AIIMS Bhopal",
         "url": "https://example.invalid/1", "rel": "high"},
    ]
    found = 0
    for it in items:
        if scraper._off_scope(it.get("title", "")):
            continue
        found += 1
        db.upsert_listing({
            "source_id": "t", "source_name": "T", "region": "R", "category": "C",
            "title": it["title"], "url": it["url"], "relevance": it["rel"],
            "snippet": "", "is_seed": 0, "notice_date": None, "deadline": None,
            "document_type": "UNKNOWN",
        })

    import sqlite3
    rows = [r[0] for r in sqlite3.connect(db.DB_PATH).execute("SELECT title FROM listings")]
    assert found == 1
    assert rows == ["Senior Resident (Pathology) at AIIMS Bhopal"]

# ---------------------------------------------------------------------------
# Cross-site write guard, digest link safety, opt-in proxy trust
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/api/flag", "/api/refresh"])
def test_cross_origin_post_is_rejected(path):
    c = app_module.app.test_client()
    r = c.post(path, json={}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert r.get_json()["error"] == "cross-origin request rejected"


def test_sec_fetch_site_cross_site_is_rejected():
    c = app_module.app.test_client()
    r = c.post("/api/flag", json={}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_same_origin_and_headerless_posts_are_not_blocked_by_guard():
    c = app_module.app.test_client()
    same = c.post("/api/flag", json={}, headers={"Origin": "http://localhost"})
    bare = c.post("/api/flag", json={})
    # Passes the guard and is rejected later by payload validation (400), not 403.
    assert same.status_code == 400
    assert bare.status_code == 400


def test_cross_origin_does_not_consume_rate_limit(monkeypatch):
    monkeypatch.setitem(app_module._RATE_LIMITED_PATHS, "/api/flag", 2)
    c = app_module.app.test_client()
    for _ in range(5):
        assert c.post("/api/flag", json={},
                      headers={"Origin": "http://evil.example"}).status_code == 403
    assert c.post("/api/flag", json={}).status_code == 400  # budget intact


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,x", "  JavaScript:x"])
def test_digest_does_not_link_non_http_urls(url):
    import alerts
    out = alerts._digest_html([{"region": "R", "url": url, "title": "T",
                                "source_name": "S", "category": "C"}])
    # The footer carries one static dashboard link; the listing must add none.
    assert out.count("<a ") == 1
    assert url.strip().lower() not in out.lower()
    assert ">T</span>" in out


def test_digest_links_http_urls():
    import alerts
    out = alerts._digest_html([{"region": "R", "url": "https://x.gov.in/a?b=1&c=2",
                                "title": "T", "source_name": "S", "category": "C"}])
    assert 'href="https://x.gov.in/a?b=1&amp;c=2"' in out


def test_proxy_trust_is_off_by_default():
    assert config.TRUST_PROXY is False
    from werkzeug.middleware.proxy_fix import ProxyFix
    assert not isinstance(app_module.app.wsgi_app, ProxyFix)
