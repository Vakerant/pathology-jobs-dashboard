"""
Generic daily scraper with date extraction.

For every source it fetches the page, walks all links, and keeps anything whose
link text OR surrounding row mentions PATHOLOGY. It also pulls any date from the
row (publish / walk-in / last-date) so the dashboard can show "uploaded on" and
hide expired notices. Each source is isolated in try/except.
"""
import re
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, date
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

import db
from sources import SOURCES, RECRUIT_KEYWORDS, PATHOLOGY_KEYWORDS, STOPWORDS, JUNK_TITLES

warnings.filterwarnings("ignore")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
}
TIMEOUT = (10, 30)          # (connect, read) — govt sites are slow but do answer
MAX_ITEMS_PER_SOURCE = 60
MAX_WORKERS = 10


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
    ("dMy", re.compile(r"\b(\d{1,2})[ .\-]*([A-Za-z]{3,9})[ .,\-]+(20\d{2})\b")),  # 5 June 2026
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
    r"(last\s*date|closing\s*date|apply\s*(?:by|before|on\s*or\s*before|up\s*to|upto)"
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
    # Corrigendum / addendum notices (not actual vacancies)
    "corrigendum:", "addendum:", "extension of application",
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


def relevance(text):
    """Judge from the LINK's own text+href only (NOT shared row context)."""
    t = text.lower()
    if any(n in t for n in NEGATIVE):
        return None
    if _NEGATIVE_EXACT.search(t):
        return None
    if any(k in t for k in PATHOLOGY_KEYWORDS):
        return "high"
    if any(k in t for k in RECRUIT_KEYWORDS) and len(t) >= 12:
        return "medium"
    return None


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
        # A title still stuck at "View Details" means no usable context either.
        if len(disp) < 10 or disp.lower() in JUNK_TITLES:
            continue
        nd, dl = extract_dates(ctx)
        if not nd and not dl:
            nd, dl = extract_dates(title)
        k = (disp.lower(), url)
        if k in seen:
            continue
        seen.add(k)
        yield {"title": disp, "url": url, "rel": rel, "notice_date": nd, "deadline": dl}


def scrape_source(src, session=None):
    new_count = 0
    sess = session or _session()
    try:
        resp = sess.get(src["url"], timeout=TIMEOUT, verify=False)
        http = resp.status_code
        if http >= 400:
            db.record_status(src["id"], src["name"], src["region"], "failed", http, 0, f"HTTP {http}")
            return 0, f"HTTP {http}"
        items = list(_candidates(resp.text, resp.url))[:MAX_ITEMS_PER_SOURCE]
        found = 0
        for it in items:
            found += 1
            is_new = db.upsert_listing({
                "source_id": src["id"], "source_name": src["name"],
                "region": src["region"], "category": src["category"],
                "title": it["title"], "url": it["url"], "relevance": it["rel"],
                "snippet": "", "is_seed": 0, "notice_date": it["notice_date"],
                "deadline": it["deadline"],
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
            if verbose:
                print(f"  ✗ {src['name']:<42} {err[:60]}")
        else:
            ok += 1
            if verbose:
                print(f"  ✓ {src['name']:<42} (+{new} new)")
    pruned = db.prune_stale()
    db.set_meta("last_run", datetime.now(timezone.utc).isoformat())
    db.set_meta("last_run_new", total_new)
    db.set_meta("last_run_ok", ok)
    db.set_meta("last_run_failed", failed)
    if verbose:
        print(f"\nDone. {ok} sources OK, {failed} failed, {total_new} new listings"
              f"{f', {pruned} stale pruned' if pruned else ''}.")
    return total_new


if __name__ == "__main__":
    run(verbose="-q" not in sys.argv)
