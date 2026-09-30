"""
Generic daily scraper with date extraction.

For every source it fetches the page, walks all links, and keeps anything whose
link text OR surrounding row mentions PATHOLOGY. It also pulls any date from the
row (publish / walk-in / last-date) so the dashboard can show "uploaded on" and
hide expired notices. Each source is isolated in try/except.
"""
import re
import sys
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, date
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

import db
import config
from sources import (SOURCES, RECRUIT_KEYWORDS, PATHOLOGY_KEYWORDS, JUNK_TITLES,
                    OFF_SCOPE_PATHS)

# PDF text extraction is an optional capability: the scraper must keep working
# (just with shallower detection) when the dependency is absent, e.g. on a
# partially provisioned box. CI installs requirements.txt, which pins pypdf.
try:
    import pypdf
except Exception:  # noqa: BLE001 — ImportError, or a broken native install
    pypdf = None

if pypdf is not None:
    # pypdf chatters at WARNING about font encoding, rotated text and duplicate
    # /Info keys on ordinary government notices. None of it changes the text we
    # match against, and scrape.log is an operator surface — keep it for real
    # problems. Unparseable documents are already handled by _pdf_text()'s
    # try/except.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    logging.getLogger("pypdf._reader").setLevel(logging.ERROR)

log = logging.getLogger("scraper")
# Only suppress InsecureRequestWarning if insecure hosts are explicitly allowed
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}
TIMEOUT = (10, 30)          # (connect, read) — govt sites are slow but do answer
MAX_ITEMS_PER_SOURCE = 60
MAX_WORKERS = 10

# Hosts that genuinely need verify=False (broken cert chain). Default empty — secure by default.
# Merged with config.INSECURE_HOSTS (which carries the built-in govt defaults)
# plus any PATHO_INSECURE_HOSTS env overrides.
_INSECURE_HOSTS = {h.strip().lower() for h in
                   (set(os.environ.get("PATHO_INSECURE_HOSTS", "").split(",")) | config.INSECURE_HOSTS)
                   if h.strip()}

def _is_insecure_host(url: str) -> bool:
    try:
        host = url.split("/")[2].lower().split(":")[0]
    except Exception:
        return False
    return host in _INSECURE_HOSTS or any(host.endswith("." + h) or host == h for h in _INSECURE_HOSTS)


def _session():
    s = requests.Session()
    retry = Retry(total=3, connect=2, backoff_factor=2,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET",))
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update(HEADERS)
    return s

# ---------------- date extraction ----------------
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DATE_PATS = [
    ("dmy", re.compile(r"\b(\d{1,2})[.\-/](\d{1,2})[.\-/](20\d{2})\b")),          # 05.06.2026
    # The optional ordinal suffix matters: Indian government notices write
    # "5th October, 2026" far more often than "5 October 2026", and without it
    # those dates are silently invisible to every date extractor here.
    ("dMy", re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?[ .\-]*([A-Za-z]{3,9})[ .,\-]+(20\d{2})\b")),  # 5th June 2026
    ("ymd", re.compile(r"\b(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})\b")),          # 2026-06-05
]


def _mk(y, m, d):
    try:
        if 2023 <= y <= 2028 and 1 <= m <= 12 and 1 <= d <= 31:
            return date(y, m, d)
    except ValueError:
        pass
    return None


# Words that, appearing shortly before a date, mark it as a DEADLINE / event
# date (last date to apply, walk-in date) rather than a publish date.
_DEADLINE_HINT = re.compile(
    r"(last\s*date|closing\s*date|clos(?:e|es|ing)\s*on|apply\s*(?:by|before|on\s*or\s*before|up\s*to|upto)"
    r"|on\s*or\s*before|walk[\s\-]?in|interview\s*(?:on|date|dated|scheduled)"
    r"|up\s*to|upto|till|extended\s*(?:to|till|upto)|deadline)",
    re.I)


def _iter_dates(text):
    """Yield (date, start_pos) for every plausible date in text."""
    for kind, pat in _DATE_PATS:
        for m in pat.finditer(text):
            g = m.groups()
            if kind == "dmy":
                d = _mk(int(g[2]), int(g[1]), int(g[0]))
            elif kind == "ymd":
                d = _mk(int(g[0]), int(g[1]), int(g[2]))
            else:
                mon = _MONTHS.get(g[1][:3].lower())
                d = _mk(int(g[2]), mon, int(g[0])) if mon else None
            if d:
                yield d, m.start()


def extract_dates(text):
    """Return (notice_date, deadline) as ISO strings (either may be None).

    A date preceded (within ~50 chars) by a deadline-ish label — "last date",
    "walk-in", "on or before" … — counts as the deadline. Everything else is
    treated as a publish/notice date."""
    if not text:
        return None, None
    plain, labelled = [], []
    for d, pos in _iter_dates(text):
        window = text[max(0, pos - 50):pos]
        (labelled if _DEADLINE_HINT.search(window) else plain).append(d)
    deadline = max(labelled).isoformat() if labelled else None
    notice = max(plain).isoformat() if plain else None
    return notice, deadline


def extract_latest_date(text):
    """Back-compat: latest plausible date in text (ISO) or None."""
    nd, dl = extract_dates(text)
    return max(filter(None, [nd, dl]), default=None)


def _clean(text):
    return re.sub(r"\s+", " ", (text or "")).strip()


# Kill catalog pages / unrelated specialities even if a neighbour mentions pathology.
# Links to these hosts are never job notices (footer/social boilerplate).
BAD_HOSTS = ("youtube.com", "youtu.be", "facebook.com", "twitter.com", "x.com",
             "instagram.com", "linkedin.com", "whatsapp.com", "t.me", "goo.gl",
             "maps.google", "play.google")

NEGATIVE = [
    "total visitor", "visitor count", "all rights reserved",
    "test", "package", "checkup", "health-pack", "price", "book-", "/book", "cart",
    "symptom", "disease", "treatment", "/blog", "article", "faq", "sitemap", "login",
    "biochem", "uro-onco", "onco-surg", "oncosurg", "radiolog", "anaesth", "anesth",
    "orthop", "cardiolog", "gynae", "obg", "dermatolog", "nephrolog", "neurosurg",
    "psychiatr", "ophthalm", "paediatric onco", "pediatric onco", "cytogenetic",
    "biochemistry", "microbiolog", "department of lab",
    "cme", "workshop", "conference", "webinar", "reporting form", "reaction reporting",
    "seniority", "promotion as", "guideline", "tariff", "syllabus", "curriculum",
    "feedback", "reporting-form", "registration form",
    # Results / admin notices — not job postings
    "result of selection", "result for the post", "interview result",
    "final result", "shortlisted candidate", "eligible list", "in-eligible",
    "written exam notice", "annexure", "/roster/",
    # Non-recruitment admin pages
    "president - institute", "president - cib", "non-faculty group",
    # Sales / non-clinical roles
    "sales manager", "territory sales", "business development",
    # Nursing-specific (not pathology)
    "tutor (nursing)", "nursing tutor",
]

# Patterns in link text that indicate a false RECRUIT match.
# These prevent words like "resident" inside "president" or
# "faculty" inside "non-faculty" from triggering false positives.
# NOTE: only short/bare titles trip these; a long meaningful title
# rarely matches the exact phrase forms below.
_NEGATIVE_EXACT = re.compile(
    r"\bpresident\b"                         # bare navigation: "President - Institute"
    r"|\bnon-faculty\b|\bnon faculty\b"      # "Non-Faculty" nav links (not long ads)
    r"|\btutor \(nursing\)\b|nursing tutor"  # nursing-specific tutor links
    r"|/roster/",                            # roster PDF links
    re.I,
)


def _off_scope(text):
    """True when text is pathology-ADJACENT but NOT MD (Human) Pathology.

    Checked before the positive PATHOLOGY_KEYWORDS test, because terms like
    "oral pathologist" / "veterinary pathologist" contain "patholog" and would
    otherwise be scored as high-relevance MD pathology."""
    t = (text or "").lower()
    return any(k in t for k in OFF_SCOPE_PATHS)


def relevance(text):
    """Judge from the LINK's own text+href only (NOT shared row context)."""
    t = text.lower()
    if any(n in t for n in NEGATIVE):
        return None
    if _NEGATIVE_EXACT.search(t):
        return None
    # Strict scope: MD pathology only. An oral/dental/veterinary notice is
    # out of scope even though it is a genuine recruitment notice.
    if _off_scope(t):
        return None
    if any(k in t for k in PATHOLOGY_KEYWORDS):
        return "high"
    if any(k in t for k in RECRUIT_KEYWORDS) and len(t) >= 12:
        return "medium"
    return None


# document_type values MUST mirror the documents.document_type enum defined in
# migrations._migrate_phase2_domain: ADVERTISEMENT | CORRIGENDUM | ADDENDUM |
# EXTENSION | INTERVIEW_NOTICE | SHORTLIST | RESULT | CANCELLATION |
# RECRUITMENT_RULE | UNKNOWN.
DOCUMENT_TYPE_RULES = [
    ("CANCELLATION",     (r"\bcancel",)),
    ("EXTENSION",        (r"\bextension\b", r"\bextended\b", r"\bpostponed\b", r"\bre-scheduled\b", r"\brescheduled\b")),
    ("CORRIGENDUM",      (r"\bcorrigendum\b", r"\bcorrection\b", r"\bamendment\b", r"\berrata\b", r"\bmodification\b")),
    ("ADDENDUM",         (r"\baddendum\b", r"\baddenda\b", r"\bclarification\b")),
    ("INTERVIEW_NOTICE", (r"\binterview\b", r"\bwalk[-\s]?in\b", r"\bdocument verification\b")),
    ("SHORTLIST",        (r"\bshortlist", r"\bprovisional list\b", r"\beligible candidates\b", r"\beligibility list\b")),
    ("RESULT",           (r"\bresult\b", r"\bselected candidates\b", r"\bfinal merit\b", r"\bmerit list\b")),
    ("RECRUITMENT_RULE", (r"\brecruitment rules\b", r"\brecruitment & assessment\b", r"\bscheme of examination\b")),
    ("ADVERTISEMENT",    (r"\bwalk[-\s]?in\s+interview\b", r"\bapplications? are invited\b", r"\badvt", r"\badvertisement\b", r"\bvacanc", r"\brecruitment\b")),
]
_COMPILED_DOC_RULES = [
    (doc_type, [re.compile(pat, re.I) for pat in pats])
    for doc_type, pats in DOCUMENT_TYPE_RULES
]


def classify_document_type(text):
    """Classify a recruitment document from its link/row text.

    Returns one of the document_type enum values (see DOCUMENT_TYPE_RULES).
    Ordering encodes precedence: explicit amendments (corrigendum/addendum/
    extension/cancellation) win over lifecycle notices (interview/shortlist/
    result) which win over a plain advertisement. Defaults to UNKNOWN when no
    signal matches — never guesses.
    """
    t = _clean(text)
    if not t:
        return "UNKNOWN"
    for doc_type, pats in _COMPILED_DOC_RULES:
        for pat in pats:
            if pat.search(t):
                return doc_type
    return "UNKNOWN"


def _row_text(a):
    """Climb to the nearest table-row / list-item / paragraph for context."""
    node = a
    for _ in range(4):
        if node.parent is None:
            break
        node = node.parent
        if node.name in ("tr", "li", "p", "div"):
            txt = _clean(node.get_text(" "))
            if 15 < len(txt) < 500:
                return txt
    return _clean(a.get_text())


def _candidates(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    seen = set()

    for a in soup.find_all("a"):
        title = _clean(a.get_text())
        href = a.get("href") or ""
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url = urljoin(base_url, href)
        if any(h in url.lower() for h in BAD_HOSTS):
            continue
        rel = relevance(f"{title} {href}")   # link's own signal only
        if not rel:
            continue
        ctx = _row_text(a)
        if any(n in ctx.lower() for n in ("total visitor", "all rights reserved")):
            continue
        # Pick a meaningful title: prefer the row context if the link text is junk.
        disp = title
        if title.lower() in JUNK_TITLES or len(title) < 5:
            disp = ctx if (ctx and ctx.lower() not in JUNK_TITLES and len(ctx) >= 5) \
                else (title or href.split("/")[-1])
        disp = db.strip_serial(disp)[:240]
        # Keep only substantive, actionable notices (real links, real titles).
        # A title still stuck at "View Details" means no usable context either —
        # UNLESS the target is a document. A notice PDF is routinely linked as
        # bare "View Document", and its real title can only come from inside
        # the document itself (_enrich_from_pdf). Dropping it here would hide
        # the notice entirely, which is the exact bug this layer exists to fix.
        if len(disp) < 10 or (disp.lower() in JUNK_TITLES and not _is_pdf(url)):
            continue
        nd, dl = extract_dates(ctx)
        if not nd and not dl:
            nd, dl = extract_dates(title)
        k = (disp.lower(), url)
        if k in seen:
            continue
        seen.add(k)
        yield {
            "title": disp, "url": url, "rel": rel,
            "notice_date": nd, "deadline": dl,
            "document_type": classify_document_type(f"{title} {href} {ctx}"),
        }


# ---------------- document discovery ----------------
# Why this section exists
# ----------------------
# Many institutional sites keep the *links* to a notice on a sub-page, and the
# notice itself on a different host entirely. AIIMS Jodhpur, for example, links
# "Senior Residents" to a rolling `residents-rec.php` page, which in turn links
# the actual advertisement PDFs on `rec.aiimsjodhpur.edu.in`. A one-page scraper
# therefore sees a link named "View Document" and stops — the department-wise
# vacancy table (the only place "Pathology" appears) lives inside the PDF.
#
# Three narrow additions close that gap without loosening anything else:
#   1. follow a small number of same-site *index* links,
#   2. extract text from linked PDFs,
#   3. re-judge such a document on its own body text.

MAX_INDEX_FOLLOWS = 3      # per source; each costs one more request
MAX_PDF_BYTES = 12 * 1024 * 1024
MAX_PDF_PAGES = 8
MAX_PDF_ENRICH = 12        # per source; PDF parsing is the expensive part

# A link is worth following as an index only if its text or href smells like a
# recruitment landing page. Deliberately narrow — see _index_links().
_INDEX_HINT = re.compile(
    r"recruit|vacan|career|opportunit|announcement|advertis|senior[\s_\-]?resident"
    r"|residents?[\s_\-]?rec|jr[\s_\-]?rec|job|opening|notification|notice|apply",
    re.I)

_PDF_RE = re.compile(r"\.pdf(?:[?#]|$)", re.I)

# "Subject:" lines carry the real intent of an official notice. PDF text arrives
# already whitespace-flattened, so a label-anchored terminator is the reliable
# stop; a first-sentence fallback covers notices that simply end there.
# Sentence ends are only honoured before an uppercase word, otherwise
# "Govt. of India's Residency Scheme" would be cut at the abbreviation.
_SUBJECT_RE = re.compile(
    r"Subject\s*[:\-]\s*"
    r"(?:(?P<labelled>.{15,200}?)(?=\s{2,}|\s(?:Ref|Copy to|Dated|Advt|Advertisement|Venue|Reporting|To)\b"
    r"|\b(?:Advertisement|Advt)\.?\s*(?:No|Number)\b)"
    r"|"
    r"(?P<sentence>.{15,200}?)(?=[.;]\s+(?-i:[A-Z0-9(])|$))",
    re.I)

# Lines that are pure furniture, in priority order, when hunting for a title.
_TITLE_NOISE = re.compile(
    r"^\s*(?:page\s*\d|\d+\s*of\s*\d+|table\s*\d|fig(?:ure)?\.?\s*\d|-{3,}|_{3,}"
    r"|\*{3,}|www\.|https?://|advertisement\s*(?:no|number)?\s*[:.]?\s*$)", re.I)

# A leading run of page furniture glued to the first real sentence. `_pdf_text`
# flattens all whitespace, so the fallback's `\s{3,}` split never fires on real
# input and a chunk beginning "Page 1 of 4 AIIMS JODHPUR ..." would be rejected
# whole. Strip the furniture once, then judge what remains.
_LEADING_FURNITURE = re.compile(
    r"^\s*(?:page\s*\d+(?:\s*of\s*\d+)?|table\s*\d+|fig(?:ure)?\.?\s*\d+"
    r"|annex(?:ure)?\s*\d*|https?://\S+|www\.\S+)\s*[-–—:|.]*\s*",
    re.I)

# Trailing link furniture. When a notice link sits in a table row, `_row_text`
# glues the whole row into the title, so the "title" ends up as
# "... at AIIMS, Jodhpur (Rajasthan) INDIA. View Document 24-09-2026 05-10-2026
# Apply Live Online". Those trailing tokens are the link's own affordances, never
# part of the notice's subject, and every date among them has already been
# extracted into notice_date/deadline by extract_dates(). Strip them so the card
# leads with the actual recruitment.
_TRAILING_FURNITURE = re.compile(
    r"(?:\s*[.·|,]?\s*(?:"
    r"view\s+(?:document|details?|detail|notice|advertisement|advt|pdf|file)"
    r"|open\s+(?:document|pdf|link)"
    r"|click\s+(?:here\s+)?to\s+(?:view|download|apply)"
    r"|read\s+more|download(?:\s+pdf)?|apply(?:\s+live)?(?:\s+online)?|live\s+online"
    r"|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}"
    r"|\d{1,2}\s*(?:st|nd|rd|th)?\s+[A-Za-z]{3,9},?\s*20\d{2}"
    r"))+\s*$",
    re.I)

# Refuse to trim to less than this: a title this short has lost its subject.
_MIN_TRIMMED_TITLE = 30


def _trim_link_furniture(title):
    """Drop trailing link affordances/dates from a row-context-derived title."""
    if not title:
        return title
    out = title
    while True:
        trimmed = _TRAILING_FURNITURE.sub("", out).strip(" .·|,;-–—")
        if trimmed == out or len(trimmed) < _MIN_TRIMMED_TITLE:
            return out
        out = trimmed


def _is_pdf(url):
    return bool(_PDF_RE.search(url or ""))


def _registrable(host):
    """Reduce a hostname to the domain its owner registers, so subdomains
    (`rec.aiimsjodhpur.edu.in`) compare equal to the parent (`aiimsjodhpur.edu.in`)
    while unrelated organisations do not. Handles `*.ac.in`, `*.gov.in` and
    ordinary two-label domains."""
    parts = [p for p in (host or "").lower().strip(".").split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    # Walk left until the remaining labels form a known public suffix boundary.
    for i in range(1, len(parts) - 1):
        tail = ".".join(parts[i:])
        if tail.split(".")[0] in ("ac", "gov", "edu", "nic", "org", "net", "co", "gen", "res"):
            return ".".join(parts[i - 1:])
    return ".".join(parts[-2:])


def _same_site(a, b):
    """True when two URLs belong to the same organisation's domain tree."""
    try:
        ha, hb = urlparse(a).hostname, urlparse(b).hostname
    except Exception:  # noqa: BLE001
        return False
    if not ha or not hb:
        return False
    ra, rb = _registrable(ha), _registrable(hb)
    if not ra or not rb:
        return False
    return ra == rb


def _index_links(html, base_url, limit=MAX_INDEX_FOLLOWS):
    """Return up to `limit` same-site, non-PDF index URLs worth a second request.

    Filters on link text + href (cheap, and the only signal available without
    fetching), then keeps the earliest occurrences in document order so the
    most prominent recruitment link wins."""
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a"):
        if len(out) >= limit:
            break
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        try:
            url = urljoin(base_url, href)
        except Exception:  # noqa: BLE001
            continue
        if not url.lower().startswith(("http://", "https://")):
            continue
        if _is_pdf(url) or url in seen:
            continue
        if any(h in url.lower() for h in BAD_HOSTS):
            continue
        if not _same_site(url, base_url):
            continue
        signal = f"{_clean(a.get_text())} {href}"
        if not _INDEX_HINT.search(signal):
            continue
        seen.add(url)
        out.append(url)
    return out


def _pdf_bytes(url, session):
    """Download a document with a hard size cap. None on any failure."""
    verify = not _is_insecure_host(url)
    resp = None
    try:
        resp = session.get(url, timeout=TIMEOUT, verify=verify, stream=True)
        if resp.status_code >= 400:
            return None
        buf = bytearray()
        for chunk in resp.iter_content(65536):
            buf.extend(chunk)
            if len(buf) > MAX_PDF_BYTES:
                log.warning("pdf too large, skipping: %s", url)
                return None
        return bytes(buf)
    except Exception as exc:  # noqa: BLE001
        log.debug("pdf download failed %s: %s", url, exc)
        return None
    finally:
        # The response is streamed, so it holds the connection until closed.
        # Without this every rejected or oversized document leaked a socket.
        # `session` is injectable, so tolerate a response shim without close().
        closer = getattr(resp, "close", None)
        if closer is not None:
            closer()


def _pdf_text(url, session):
    """Flattened text of a PDF's first few pages, or None.

    Two details are load-bearing:
      * pypdf's default mode breaks words at the PDF's own line breaks, so
        "Residency Scheme" comes out as "Resid ency Scheme" and lands in the
        middle of user-facing titles. `extraction_mode="layout"` re-joins them.
        It costs roughly 60% more CPU, which is why the per-source PDF budget
        (`MAX_PDF_ENRICH`) stays small.
      * whatever it returns is whitespace-flattened before reaching any keyword
        matcher, because line breaks are not word boundaries."""
    if pypdf is None:
        return None
    raw = _pdf_bytes(url, session)
    if not raw:
        return None
    try:
        import io
        reader = pypdf.PdfReader(io.BytesIO(raw))
        pages = reader.pages[:MAX_PDF_PAGES]

        def _extract(m):
            return [p.extract_text(extraction_mode=m) or "" for p in pages]

        try:
            parts = _extract("layout")
        except (TypeError, ValueError):  # pypdf < 4 has no layout mode
            parts = [p.extract_text() or "" for p in pages]
    except Exception as exc:  # noqa: BLE001
        log.debug("pdf parse failed %s: %s", url, exc)
        return None
    text = re.sub(r"\s+", " ", " ".join(parts)).strip()
    return text or None


def _mentions_pathology(text):
    """Pathology-only signal, deliberately WITHOUT the NEGATIVE gate.

    `relevance()` returns None on full advertisement bodies because they are
    full of words like "result" and "annexure" — correct for a *link title*,
    wrong for a *document body*. A recruitment notice whose text names a
    pathology department is relevant to a pathologist no matter what else the
    notice contains."""
    t = (text or "").lower()
    if _off_scope(t):
        return False
    return any(k in t for k in PATHOLOGY_KEYWORDS)


def _pathology_sentence(text, width=180):
    """The first sentence-ish span mentioning pathology — used as the snippet,
    because that is the line carrying the actual vacancy."""
    t = re.sub(r"\s+", " ", text or "").strip()
    if not t:
        return None
    if _off_scope(t):
        return None
    low = t.lower()
    best = min((low.find(k) for k in PATHOLOGY_KEYWORDS if k in low), default=-1)
    if best < 0:
        return None
    start = max(0, best - 40)
    # Snap forward to the next space so the snippet never starts mid-word.
    # Adding a fixed 1 here only ever advanced the cut by a single character,
    # which reliably split words such as "Microbiology" into "biology".
    if start:
        nxt = t.find(" ", start)
        if nxt != -1:
            start = nxt + 1
    end = min(len(t), start + width)
    return t[start:end].strip() or None


def _pdf_subject(text):
    """A real display title for a document, or None if nothing usable.

    Prefers an explicit `Subject:` line; otherwise takes the first line that
    looks like prose about recruitment rather than page furniture."""
    t = re.sub(r"[ \t]+", " ", (text or "")).strip()
    if not t:
        return None
    m = _SUBJECT_RE.search(t)
    if m:
        cand = _clean((m.group("labelled") or m.group("sentence") or "")).strip(" .;-–—")
        if len(cand) >= 15:
            return cand
    # Fall back to the first substantive chunk, flattened. Split only before an
    # uppercase word so abbreviations ("Govt. of") do not end a chunk.
    for chunk in re.split(r"(?<=[.;])\s+(?=[A-Z0-9(])|\s{3,}", t):
        cand = _clean(_LEADING_FURNITURE.sub("", chunk, count=1)).strip(" .;-–—")
        if len(cand) < 15 or len(cand) > 220:
            continue
        if _TITLE_NOISE.match(cand):
            continue
        if not re.search(r"recruit|senior resident|residenc|post\b|postings?|vacanc|notice|advt|advertisement|appointment", cand, re.I):
            continue
        return cand
    return None


def _pdf_deadline(text, window=120):
    """Deadline from document text, using a WIDER hint window than extract_dates().

    extract_dates() looks ~50 chars back for a label, which works for a table
    cell but misses prose like "The last date for applying for the post of
    Senior Residents is 5th October, 2026" where the label sits ~76 chars away.
    Returns the LATEST such date, matching extract_dates' semantics."""
    t = re.sub(r"\s+", " ", text or "")
    if not t:
        return None
    hits = [d for d, pos in _iter_dates(t)
            if _DEADLINE_HINT.search(t[max(0, pos - window):pos])]
    return max(hits).isoformat() if hits else None


def _enrich_from_pdf(item, session):
    """Promote a document-link candidate using the document's own text.

    Only ever ADDS signal: relevance is promoted high when the body names a
    pathology department, an uninformative title is replaced by the notice's
    real subject, and a snippet/deadline is filled in. A document that says
    nothing about pathology is left exactly as it was."""
    url = item.get("url") or ""
    if not _is_pdf(url):
        return False
    body = _pdf_text(url, session)
    if not body:
        return False
    changed = False
    if not _mentions_pathology(body):
        return False
    subject = _pdf_subject(body)
    if subject and (not item.get("title") or item["title"].lower() in JUNK_TITLES):
        item["title"] = db.strip_serial(subject)[:240]
        changed = True
    else:
        # Row-context titles keep the link's own affordances; strip them.
        tidied = db.strip_serial(_trim_link_furniture(item.get("title") or ""))[:240]
        if tidied and tidied != item.get("title"):
            item["title"] = tidied
            changed = True
    snippet = _pathology_sentence(body)
    if snippet:
        item["snippet"] = snippet[:300]
        changed = True
    if not item.get("deadline"):
        dl = _pdf_deadline(body)
        if dl:
            item["deadline"] = dl
            changed = True
    if item.get("rel") != "high":
        item["rel"] = "high"
        changed = True
    # A body that names a department is still an advertisement even when the
    # link text only said "Advertisement".
    if item.get("document_type") in (None, "UNKNOWN"):
        item["document_type"] = classify_document_type(body[:2000])
        changed = True
    return changed


def _collect(source_html, source_url, session):
    """Homepage candidates + candidates from a few followed index pages.

    The per-source item budget is applied AFTER merging so a link-heavy index
    page cannot starve the homepage's own notices."""
    items, seen = [], set()
    for it in _candidates(source_html, source_url):
        k = (it["title"].lower(), it["url"])
        if k in seen:
            continue
        seen.add(k)
        items.append(it)
    for link in _index_links(source_html, source_url):
        if len(items) >= MAX_ITEMS_PER_SOURCE:
            break
        try:
            verify = not _is_insecure_host(link)
            r = session.get(link, timeout=TIMEOUT, verify=verify)
            if r.status_code >= 400:
                log.info("index %s -> HTTP %s", link, r.status_code)
                continue
        except Exception as exc:  # noqa: BLE001
            log.info("index %s -> %s", link, type(exc).__name__)
            continue
        for it in _candidates(r.text, r.url):
            k = (it["title"].lower(), it["url"])
            if k in seen:
                continue
            seen.add(k)
            items.append(it)
    return items[:MAX_ITEMS_PER_SOURCE]


def scrape_source(src, session=None):
    new_count = 0
    sess = session or _session()
    try:
        verify = False if _is_insecure_host(src["url"]) else True
        if not verify:
            log.warning("TLS verification disabled for %s (allowlisted via PATHO_INSECURE_HOSTS)", src["url"])
        resp = sess.get(src["url"], timeout=TIMEOUT, verify=verify)
        http = resp.status_code
        if http >= 400:
            db.record_status(src["id"], src["name"], src["region"], "failed", http, 0, f"HTTP {http}")
            return 0, f"HTTP {http}"
        items = _collect(resp.text, resp.url, sess)
        # PDF enrichment is best-effort and bounded: one unreadable document must
        # never take a whole source down.
        if pypdf is not None:
            budget = MAX_PDF_ENRICH
            for it in items:
                if budget <= 0:
                    break
                if not _is_pdf(it.get("url", "")):
                    continue
                budget -= 1
                try:
                    _enrich_from_pdf(it, sess)
                except Exception as exc:  # noqa: BLE001
                    log.info("pdf enrich failed %s: %s", it.get("url"), type(exc).__name__)
        found = 0
        for it in items:
            found += 1
            is_new = db.upsert_listing({
                "source_id": src["id"], "source_name": src["name"],
                "region": src["region"], "category": src["category"],
                "title": it["title"], "url": it["url"], "relevance": it["rel"],
                "snippet": it.get("snippet", ""), "is_seed": 0,
                "notice_date": it["notice_date"],
                "deadline": it["deadline"],
                "document_type": it.get("document_type", "UNKNOWN"),
            })
            if is_new:
                new_count += 1
        db.record_status(src["id"], src["name"], src["region"], "ok", http, found, "")
        return new_count, None
    except Exception as e:  # noqa: BLE001
        db.record_status(src["id"], src["name"], src["region"], "failed", 0, 0, str(e)[:300])
        return 0, str(e)


def run(verbose=True):
    db.init_db()
    total_new = 0
    ok, failed = 0, 0
    # Open a run row up-front so a crashed or killed run is still visible in
    # `scrape_runs` (status 'running') instead of vanishing with no trace.
    run_id = f"run-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    started = datetime.now(timezone.utc).isoformat()
    db.record_run_start(run_id, started, len(SOURCES))
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(scrape_source, src, _session()): src for src in SOURCES}
        results = {}
        for fut in as_completed(futures):
            results[futures[fut]["id"]] = fut.result()
    for src in SOURCES:                      # report in registry order
        new, err = results[src["id"]]
        total_new += new
        if err:
            failed += 1
            db.record_source_run(run_id, src["id"], "failed", 0, 0, err[:300])
            if verbose:
                print(f"  ✗ {src['name']:<42} {err[:60]}")
        else:
            ok += 1
            http = db.get_source_http_status(src["id"])
            found = db.get_source_items_found(src["id"])
            db.record_source_run(run_id, src["id"], "ok", http, found, "")
            if verbose:
                print(f"  ✓ {src['name']:<42} (+{new} new)")
    pruned = db.prune_stale()
    # Keep the opportunity-first projection in step with the listing store.
    # Without this the mirror drifts (new listings never reach
    # `opportunities`, pruned listings leave orphans behind) and the
    # invariants documented in ARCHITECTURE.md section 4 break.
    try:
        import repository
        sync = repository.sync_opportunities_from_listings()
        superseded = repository.reconcile_orphans()
        projection = repository.verify_projection()
    except Exception as exc:  # noqa: BLE001
        log.warning("opportunity projection sync failed: %s", exc)
        sync = superseded = projection = None
    db.set_meta("last_run", datetime.now(timezone.utc).isoformat())
    db.set_meta("last_run_new", total_new)
    db.set_meta("last_run_ok", ok)
    db.set_meta("last_run_failed", failed)
    # 'partial' is the honest label when some sources failed: the listings we
    # did collect are valid and committed, so this is not a total failure.
    db.record_run_finish(
        run_id, datetime.now(timezone.utc).isoformat(),
        "success" if not failed else "partial",
        total_sources=len(SOURCES), ok=ok, failed=failed, new_items=total_new,
    )
    if projection is not None:
        db.set_meta("last_run_projection_ok", int(projection["ok"]))
        db.set_meta("last_run_orphans", projection["orphans"])
    if verbose:
        extra = ""
        if sync:
            extra = (f", projection +{sync['inserted']} new"
                     f"{f' ({superseded} superseded)' if superseded else ''}")
        print(f"\nDone. {ok} sources OK, {failed} failed, {total_new} new listings"
              f"{f', {pruned} stale pruned' if pruned else ''}{extra}.")
    return total_new


if __name__ == "__main__":
    run(verbose="-q" not in sys.argv)
