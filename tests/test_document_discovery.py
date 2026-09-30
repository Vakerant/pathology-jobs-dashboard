"""Offline tests for the document-discovery layer added in scraper.py.

The bug these guard: a real AIIMS Jodhpur "Senior Resident — Pathology &
Lab Medicine" advertisement (2 posts, last date 05-Oct-2026) was invisible in
the tracker for three compounding reasons — the homepage never followed a link
to the rolling sub-page, that sub-page's real notices live on a *different*
host (`rec.aiimsjodhpur.edu.in`), and the only place the word "Pathology"
appears is inside the PDF body, which the title/href-only `relevance()` can
never see. The fix adds index-following, a same-site gate, and PDF enrichment.

Everything here is offline and hermetic: HTML fixtures are inline, the two
functions that touch the network are exercised through a fake session, and
`_enrich_from_pdf` is exercised by stubbing the text layer so the *policy*
(which fields get promoted, and which must never be demoted) is pinned without
needing a real PDF on disk.

No corpus counts are asserted anywhere: the live corpus drifts on every scrape.
"""
from __future__ import annotations

import pytest

import scraper


# --- fixtures -----------------------------------------------------------------

# A miniature but structurally faithful reduction of the real AIIMS Jodhpur
# SR advertisement: an email-style Subject line, a department vacancy table,
# and a prose deadline whose label sits ~76 characters from the date.
JODHPUR_BODY = (
    "AIIMS JODHPUR Subject : Recruitment to the post of Senior Residents "
    "(Non DM/MCh.) as per Govt. of India's Residency Scheme at AIIMS, Jodhpur "
    "(Rajasthan) INDIA Advertisement No: Academics/SR/64/2026-AIIMS.JDH  "
    "1. Anatomy  MD 0 0 0 0 0 0  "
    "17. Pathology & Lab Medicine MD/DNB in Pathology 1 1 0 0 0 2  "
    "18. Pharmacology MD 0 1 0 0 0 1  "
    "The last date for applying for the post of Senior Residents is "
    "5th October, 2026 (Monday) (17:00 Hrs)."
)

HOMEPAGE_HTML = """
<html><body>
  <a href="/">Home</a>
  <a href="#main">Skip</a>
  <a href="mailto:jobs@aiimsjodhpur.edu.in">Mail</a>
  <a href="javascript:void(0)">Nothing</a>
  <a href="https://pmssy.mohfw.gov.in/AdHocCommittee">Ad Hoc Committees</a>
  <a href="/residents-rec.php">Residents Recruitment</a>
  <a href="/residents-rec-walkin.php">Walk-in Interview for Senior Residents</a>
  <a href="/academics/sr/64/2026-notice.html">Academics/SR/64/2026</a>
  <a href="/news/2026-symposium.pdf">Symposium brochure</a>
  <a href="https://tpc.gov.in/recruit">Other government portal</a>
  <a href="/residents-rec.php">Residents Recruitment (duplicate)</a>
  <a href="/departments/medicine/index.html">Department of Medicine</a>
</body></html>
"""

INDEX_HTML = """
<html><body>
  <a href="https://rec.aiimsjodhpur.edu.in/SR/2026/SR_2026_2/PDF/SR%20Advertisement.pdf">View Document</a>
  <a href="https://rec.aiimsjodhpur.edu.in/JR/2026/medical/JR_2026_1/PDF/Final%20Advt.%20for%20JR.pdf">View Document</a>
  <a href="https://rec.aiimsjodhpur.edu.in/notice">Announcement</a>
</body></html>
"""


class FakeResponse:
    def __init__(self, text, url, status_code=200):
        self.text = text
        self.url = url
        self.status_code = status_code


class FakeSession:
    """Serves a fixed url -> html map; records every request and can be told to
    fail for specific URLs so the swallowed-exception paths are covered."""

    def __init__(self, pages, fail=()):
        self.pages = pages
        self.fail = set(fail)
        self.requested = []

    def get(self, url, timeout=None, verify=None, stream=None, **kw):
        self.requested.append(url)
        if url in self.fail:
            raise OSError("simulated connection failure")
        body = self.pages.get(url)
        if body is None:
            return FakeResponse("", url, status_code=404)
        return FakeResponse(body, url)


# --- _is_pdf ------------------------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("https://x.in/a.pdf", True),
    ("https://x.in/A.PDF", True),
    ("https://x.in/a.pdf?v=2", True),
    ("https://x.in/a.pdf#page=3", True),
    ("https://x.in/a.pdfx", False),
    ("https://x.in/advertisement", False),
    ("https://x.in/pdf/", False),
    ("", False),
    (None, False),
])
def test_is_pdf(url, expected):
    assert scraper._is_pdf(url) is expected


# --- _registrable / _same_site ------------------------------------------------

@pytest.mark.parametrize("host,expected", [
    ("rec.aiimsjodhpur.edu.in", "aiimsjodhpur.edu.in"),
    ("aiimsjodhpur.edu.in", "aiimsjodhpur.edu.in"),
    ("www.aiimsjodhpur.edu.in", "aiimsjodhpur.edu.in"),
    ("pmssy.mohfw.gov.in", "mohfw.gov.in"),
    ("npgmc.edu.in", "npgmc.edu.in"),
    ("deep.npgmc.edu.in", "npgmc.edu.in"),
    ("example.com", "example.com"),
    ("localhost", "localhost"),
    ("", ""),
    (None, ""),
])
def test_registrable(host, expected):
    assert scraper._registrable(host) == expected


def test_same_site_allows_notice_subdomain_but_not_other_ministries():
    # This is the exact distinction the AIIMS Jodhpur fix turns on: the real
    # notices live on rec.aiimsjodhpur.edu.in, which is the same organisation.
    assert scraper._same_site(
        "https://rec.aiimsjodhpur.edu.in/SR/2026/x.pdf",
        "https://aiimsjodhpur.edu.in/",
    ) is True
    # ...while the ministry-wide committee links that decorate the same page are
    # a different organisation and must not be dragged in.
    assert scraper._same_site(
        "https://pmssy.mohfw.gov.in/AdHocCommittee",
        "https://aiimsjodhpur.edu.in/",
    ) is False


def test_same_site_rejects_unparseable_input():
    assert scraper._same_site("not a url", "https://x.in/") is False
    assert scraper._same_site("https://x.in/", "") is False


# --- _index_links -------------------------------------------------------------

def test_index_links_follows_recruitment_pages_and_nothing_else():
    urls = scraper._index_links(
        HOMEPAGE_HTML, "https://aiimsjodhpur.edu.in/")

    # Same-site recruitment landing pages are followed ...
    assert "https://aiimsjodhpur.edu.in/residents-rec.php" in urls
    assert "https://aiimsjodhpur.edu.in/residents-rec-walkin.php" in urls
    assert "https://aiimsjodhpur.edu.in/academics/sr/64/2026-notice.html" in urls

    # ...and everything else is filtered out, each for a specific reason.
    assert "https://pmssy.mohfw.gov.in/AdHocCommittee" not in urls  # off-site
    assert "https://tpc.gov.in/recruit" not in urls                 # off-site
    assert "https://aiimsjodhpur.edu.in/news/2026-symposium.pdf" not in urls  # PDF
    assert "https://aiimsjodhpur.edu.in/departments/medicine/index.html" not in urls
    assert len(urls) == len(set(urls))  # deduplicated


def test_index_links_respects_limit_and_keeps_document_order():
    urls = scraper._index_links(HOMEPAGE_HTML, "https://aiimsjodhpur.edu.in/", limit=2)
    assert len(urls) == 2
    # The first recruitment link in document order wins, not a random one.
    assert urls[0].endswith("/residents-rec.php")


def test_index_links_skips_non_http_and_empty_anchors():
    html = '<a href="mailto:a@b.in">x</a><a href="tel:+91">y</a><a href="">z</a><a>bare</a>'
    assert scraper._index_links(html, "https://aiimsjodhpur.edu.in/") == []


# --- _collect -----------------------------------------------------------------

def test_collect_merges_index_page_candidates():
    base = "https://aiimsjodhpur.edu.in/"
    index = "https://aiimsjodhpur.edu.in/residents-rec.php"
    sess = FakeSession({index: INDEX_HTML})

    items = scraper._collect(HOMEPAGE_HTML, base, sess)

    urls = [i["url"] for i in items]
    # The whole point: a notice that is only reachable one hop from the
    # homepage is now harvested.
    assert any("SR%20Advertisement.pdf" in u for u in urls)
    assert index in sess.requested
    # Still capped at the configured per-source budget.
    assert len(items) <= scraper.MAX_ITEMS_PER_SOURCE


def test_collect_survives_a_failing_index_page():
    base = "https://aiimsjodhpur.edu.in/"
    index = "https://aiimsjodhpur.edu.in/residents-rec.php"
    sess = FakeSession({}, fail={index})

    items = scraper._collect(HOMEPAGE_HTML, base, sess)
    # The homepage's own candidates are still returned.
    assert any(i["url"].endswith("/residents-rec.php") for i in items)
    assert index in sess.requested


def test_collect_dedupes_on_title_and_url(monkeypatch):
    monkeypatch.setattr(scraper, "MAX_ITEMS_PER_SOURCE", 500)
    # The same PDF is linked twice from the index page.
    html = INDEX_HTML + INDEX_HTML
    sess = FakeSession({})
    items = scraper._collect(HOMEPAGE_HTML, "https://aiimsjodhpur.edu.in/", sess)
    seen = [(i["title"].lower(), i["url"]) for i in items]
    assert len(seen) == len(set(seen))


# --- _mentions_pathology ------------------------------------------------------

def test_mentions_pathology_ignores_the_negative_gate():
    # The real AIIMS Jodhpur advertisement trips the NEGATIVE gate on ten
    # separate tokens (a written "test", plus every OTHER department it lists:
    # biochem, radiolog, anaesth, orthop, gynaec, ophthalm, biochem, microbiol,
    # guideline). relevance() therefore returns None on the body even though the
    # body names a pathology department — which is why the enrichment check is
    # pathology-only and has no negative gate.
    body = ("Result: candidates who appeared for the written test in the "
            "departments of Anatomy, Pathology, Biochemistry, Microbiology and "
            "Radiology are listed in the annexure. Guidelines follow.")
    assert scraper.relevance(body) is None
    assert scraper._mentions_pathology(body) is True


@pytest.mark.parametrize("text,expected", [
    ("MD/DNB in Pathology", True),
    ("Pathology & Lab Medicine", True),
    ("Department of Transfusion Medicine", True),
    ("Blood Bank Technician", True),
    ("Department of Medicine", False),
    ("Senior Resident in Anaesthesia", False),
    ("", False),
    (None, False),
])
def test_mentions_pathology(text, expected):
    assert scraper._mentions_pathology(text) is expected


# --- _pathology_sentence ------------------------------------------------------

def test_pathology_sentence_centres_on_the_vacancy_line():
    s = scraper._pathology_sentence(JODHPUR_BODY)
    assert s and "Pathology & Lab Medicine" in s
    # The department line carries the actual post count — that is the value the
    # user needs, so it must survive into the snippet.
    assert "1 1 0 0 0 2" in s


def test_pathology_sentence_absent_and_empty():
    assert scraper._pathology_sentence("Department of Medicine, 3 posts") is None
    assert scraper._pathology_sentence("") is None
    assert scraper._pathology_sentence(None) is None


def test_pathology_sentence_respects_width_and_word_boundary():
    body = "Padding. " * 40 + "Pathology and Lab Medicine" + " tail. " * 40
    s = scraper._pathology_sentence(body, width=60)
    assert s is not None
    assert len(s) <= 60
    assert s[0] != " "


# --- _pdf_subject -------------------------------------------------------------

def test_pdf_subject_prefers_the_explicit_subject_line():
    got = scraper._pdf_subject(JODHPUR_BODY)
    assert got is not None
    assert got.startswith("Recruitment to the post of Senior Residents")
    # The re.I case-guard matters: an abbreviation must not end the title. A
    # title truncated at the "." in "Govt." is the bug this pins down.
    assert "Residency Scheme" in got
    assert len(got) <= 200


def test_pdf_subject_stops_at_a_labelled_terminator():
    body = ("Subject : Walk-in Interview for Senior Residents "
            "Date: 01/09/2026  Ref: AIIMS/JDH/2026/44")
    got = scraper._pdf_subject(body)
    assert got is not None
    assert got.startswith("Walk-in Interview for Senior Residents")
    assert "Ref:" not in got


def test_pdf_subject_falls_back_to_the_first_substantive_line():
    body = ("Page 1 of 4  AIIMS JODHPUR Advertisement No: 1/2026  "
            "Online applications are invited for one year.")
    got = scraper._pdf_subject(body)
    assert got is not None
    assert len(got) >= 15


@pytest.mark.parametrize("text", [
    "",
    "   ",
    None,
    "Page 1 of 4",
])
def test_pdf_subject_returns_none_for_unusable_text(text):
    assert scraper._pdf_subject(text) is None


# --- _pdf_deadline ------------------------------------------------------------

def test_pdf_deadline_sees_a_label_farther_back_than_extract_dates():
    # The whole reason _pdf_deadline exists: the label sits ~76 chars away,
    # beyond extract_dates' 50-char window.
    prose = ("The last date for applying for the post of Senior Residents is "
             "5th October, 2026")
    assert scraper.extract_dates(prose)[1] is None
    assert scraper._pdf_deadline(prose) == "2026-10-05"


def test_pdf_deadline_picks_the_latest_labeled_date():
    text = ("Last date 5th October, 2026. This notice was extended; the last "
            "date is now 20th October, 2026.")
    assert scraper._pdf_deadline(text) == "2026-10-20"


def test_pdf_deadline_none_without_a_label():
    assert scraper._pdf_deadline("Published on 5th October, 2026") is None
    assert scraper._pdf_deadline("") is None
    assert scraper._pdf_deadline(None) is None


# --- ordinal dates (the regex widening) ---------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("5th October, 2026", "2026-10-05"),
    ("24th September, 2026", "2026-09-24"),
    ("1st January, 2027", "2027-01-01"),
    ("22nd March, 2026", "2026-03-22"),
    ("3rd August 2026", "2026-08-03"),
])
def test_ordinal_dates_parse(text, expected):
    assert scraper.extract_dates(text)[0] == expected


@pytest.mark.parametrize("text", [
    "regd. No. 12345 of 2026",   # a registration number, not a date
    "Room 210, 2026",            # a room number
    "version 2 of 2026",         # a version
])
def test_ordinal_widening_did_not_introduce_false_positives(text):
    assert scraper.extract_dates(text) == (None, None)


def test_ordinal_widening_is_additive():
    # Everything the old dMy pattern matched must still match.
    for text in ("5 October 2026", "05-Oct-2026", "5. October 2026"):
        assert scraper.extract_dates(text)[0] is not None


# --- _enrich_from_pdf (policy; the network/PDF layers are stubbed) -----------

def _stub_pdf_text(monkeypatch, body):
    monkeypatch.setattr(scraper, "_pdf_text", lambda url, session: body)


def test_enrich_promotes_a_junk_titled_pathology_notice(monkeypatch):
    _stub_pdf_text(monkeypatch, JODHPUR_BODY)
    item = {
        "title": "View Document",
        "url": "https://rec.aiimsjodhpur.edu.in/SR/2026/SR_2026_2/PDF/SR%20Advertisement.pdf",
        "rel": "medium",
        "document_type": "ADVERTISEMENT",
    }
    assert scraper._enrich_from_pdf(item, FakeSession({})) is True
    assert item["rel"] == "high"
    assert item["title"].startswith("Recruitment to the post of Senior Residents")
    assert "Pathology & Lab Medicine" in item["snippet"]
    assert item["deadline"] == "2026-10-05"


def test_enrich_never_demotes_an_already_high_item(monkeypatch):
    _stub_pdf_text(monkeypatch, JODHPUR_BODY)
    item = {"title": "SR Advertisement", "url": "https://x.in/a.pdf", "rel": "high"}
    scraper._enrich_from_pdf(item, FakeSession({}))
    assert item["rel"] == "high"


def test_enrich_leaves_non_pathology_documents_untouched(monkeypatch):
    _stub_pdf_text(monkeypatch, "AIIMS JODHPUR Subject: Department of Medicine. 3 posts.")
    item = {"title": "View Document", "url": "https://x.in/a.pdf", "rel": "medium"}
    assert scraper._enrich_from_pdf(item, FakeSession({})) is False
    assert item == {"title": "View Document", "url": "https://x.in/a.pdf", "rel": "medium"}


def test_enrich_ignores_non_pdf_targets(monkeypatch):
    def _boom(url, session):
        raise AssertionError("a non-PDF target must not be downloaded")

    monkeypatch.setattr(scraper, "_pdf_text", _boom)
    item = {"title": "Notice", "url": "https://x.in/notice.html", "rel": "medium"}
    assert scraper._enrich_from_pdf(item, FakeSession({})) is False


def test_enrich_keeps_a_useful_title(monkeypatch):
    _stub_pdf_text(monkeypatch, JODHPUR_BODY)
    item = {
        "title": "AIIMS Jodhpur Senior Resident notice for October 2026",
        "url": "https://x.in/a.pdf",
        "rel": "medium",
    }
    scraper._enrich_from_pdf(item, FakeSession({}))
    assert item["title"] == "AIIMS Jodhpur Senior Resident notice for October 2026"


# --- _trim_link_furniture ----------------------------------------------------
# A notice link inside a table row inherits the whole row as its "title", so it
# ends in the link's own affordances. Observed verbatim on the real AIIMS Jodhpur
# SR advertisement before this helper existed.
POLLUTED = ("Academics/SR/64/2026-AIIMS.JDH Recruitment to the post of Senior "
            "Residents (Non DM/MCh.) as per Govt. of India's Residency Scheme at "
            "AIIMS, Jodhpur (Rajasthan) INDIA. View Document 24-09-2026 "
            "05-10-2026 Apply Live Online")


@pytest.mark.parametrize("title,expected", [
    (POLLUTED, "Academics/SR/64/2026-AIIMS.JDH Recruitment to the post of "
               "Senior Residents (Non DM/MCh.) as per Govt. of India's Residency "
               "Scheme at AIIMS, Jodhpur (Rajasthan) INDIA"),
    ("Notice for Senior Residents in Pathology View Document", "Notice for Senior Residents in Pathology"),
    ("Walk-in interview for SR, department of Pathology, Jodhpur Read More",
     "Walk-in interview for SR, department of Pathology, Jodhpur"),
    ("SR Advertisement Apply", "SR Advertisement Apply"),          # too short to trim
    ("SR Advertisement Download", "SR Advertisement Download"),  # too short to trim
    ("Pathology department recruitment notice for the session 2026-27", "Pathology department recruitment notice for the session 2026-27"),
    ("", ""),
    (None, None),
])
def test_trim_link_furniture(title, expected):
    assert scraper._trim_link_furniture(title) == expected


def test_enrich_tidies_a_row_context_title(monkeypatch):
    """The pollution reaches the card even when the title is not a JUNK_TITLE,
    because the row context was informative — so the trim must run on that
    branch too, not only when _pdf_subject replaces the title."""
    _stub_pdf_text(monkeypatch, JODHPUR_BODY)
    item = {"title": POLLUTED, "url": "https://x.in/a.pdf", "rel": "medium"}
    scraper._enrich_from_pdf(item, FakeSession({}))
    assert "View Document" not in item["title"]
    assert "Apply Live Online" not in item["title"]
    assert item["title"].startswith("Academics/SR/64/2026-AIIMS.JDH Recruitment")
    assert item["rel"] == "high"


def test_enrich_keeps_an_existing_deadline(monkeypatch):
    _stub_pdf_text(monkeypatch, JODHPUR_BODY)
    item = {"title": "x", "url": "https://x.in/a.pdf", "rel": "medium", "deadline": "2026-11-01"}
    scraper._enrich_from_pdf(item, FakeSession({}))
    assert item["deadline"] == "2026-11-01"


def test_enrich_tolerates_an_empty_document(monkeypatch):
    _stub_pdf_text(monkeypatch, None)
    item = {"title": "x", "url": "https://x.in/a.pdf", "rel": "medium"}
    assert scraper._enrich_from_pdf(item, FakeSession({})) is False
    assert item["rel"] == "medium"


# --- the guarded optional dependency ------------------------------------------

def test_pdf_enrichment_is_skipped_when_pypdf_is_absent(monkeypatch):
    monkeypatch.setattr(scraper, "pypdf", None)
    sess = FakeSession({})
    assert scraper._pdf_text("https://x.in/a.pdf", sess) is None


def test_pdf_text_returns_none_for_a_failed_download():
    sess = FakeSession({}, fail={"https://x.in/a.pdf"})
    assert scraper._pdf_text("https://x.in/a.pdf", sess) is None
    assert scraper._pdf_bytes("https://x.in/a.pdf", sess) is None


def test_pdf_bytes_returns_none_for_http_error():
    sess = FakeSession({})
    assert scraper._pdf_bytes("https://x.in/missing.pdf", sess) is None
