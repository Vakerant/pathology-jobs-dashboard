"""What counts as a vacancy for this tracker.

The user holds an MD in (human) Pathology and wants exactly two kinds of post:
**Senior Resident (or an equivalent tenure post) and fellowships**, in a
pathology discipline. Everything else a recruitment page links to -- faculty
calls, technicians, project staff, nursing, junior residents, results,
eligibility lists, admission notices, department pages -- is noise.

`judge(text)` returns:
  "high"   -- an SR / fellowship notice that names a pathology discipline
  "medium" -- an SR / fellowship notice that names no department, or covers
              "various departments", so the pathology seat has to be checked
              in the document itself
  None     -- anything else

`judge_document(title, body)` re-judges a candidate once its PDF has been read:
the body either confirms a pathology seat ("high") or it does not (None).
"""
import re

# ── the post ────────────────────────────────────────────────────────────────
# "SR" alone is common in titles ("SR Advertisement", "Recruitment of SRs").
POST = re.compile(
    r"senior[\s-]*residen|\bsrs?\b|\bs\.r\.|senior[\s-]*demonstrator|"
    r"resident[\s-]*pathologist|tutor[\s-]*cum[\s-]*(?:sr|senior)|"
    r"fellowship|\bfellows?\b|post[\s-]*doctoral|\bpdcc\b|"
    r"सीनियर\s*रेजिडेंट|वरिष्ठ\s*(?:रेजिडेंट|निवासी)",
    re.I)

# ── the discipline ──────────────────────────────────────────────────────────
# Clinical haematology, transfusion medicine and blood bank are separate
# specialities (DM / MD IHTM); they only count when pathology itself is named.
PATHOLOGY = re.compile(
    r"patholog|histopath|cytopath|haemato[\s-]*path|hemato[\s-]*path|neuropath|"
    r"onco[\s-]*path|immuno[\s-]*path|laborator(?:y|ies)\s+medicine|lab\.?\s+medicine|"
    r"पैथोलॉजी|विकृति\s*विज्ञान",
    re.I)

# Pathology-adjacent but a different degree or species.
OFF_SCOPE = re.compile(
    r"oral\s*(?:&|and)?\s*(?:maxillofacial\s*)?patho|oral\s+medicin|oral\s+surg|"
    r"maxillofacial|dental|dentist|periodont|endodont|orthodont|prosthodont|"
    r"veterinar|animal\s+patholog|plant\s+patholog|fish\s+patholog|poultry",
    re.I)

# A notice covering several departments may well include pathology.
MULTI_DEPT = re.compile(
    r"various\s+(?:departments?|depts?|disciplines?|specialit|specialt|clinical|"
    r"non[\s-]*clinical|pre[\s-]*clinical|para[\s-]*clinical|broad)|"
    r"(?:different|all|multiple|several)\s+(?:departments?|disciplines?|specialit)",
    re.I)

# A department that is NOT pathology. An SR notice naming one of these (and no
# pathology word, no "various departments") is someone else's job.
OTHER_DEPT = re.compile(
    r"anaesth|anesth|transfusion|immunohaematolog|immunohematolog|blood\s+bank|"
    r"clinical\s+ha?ematolog|ha?ematolog|oncolog|radiotherap|microbiolog|biochem|"
    r"physiolog|anatomy|pharmacolog|forensic|community\s+medicine|family\s+medicine|"
    r"psychiatr|telemanas|psycholog|paediatric|pediatric|neonat|dermatolog|"
    r"venereolog|radiolog|radio[\s-]*diagnosis|nuclear\s+medicine|orthop|ophthalm|"
    r"\bent\b|otorhino|obstet|gyna?ec|\bobg\b|cardio|neurolog|neuro[\s-]*surg|"
    r"nephrolog|urolog|gastro|hepatolog|pulmon|respiratory|emergency|trauma|"
    r"general\s+medicine|general\s+surgery|internal\s+medicine|physical\s+medicine|"
    r"\bpmr\b|endocrin|rheumatolog|geriatric|plastic\s+surg|burns|vascular|"
    r"cttvs|ctvs|hospital\s+administration|dentistry|nursing|ayush|yoga|"
    r"surg(?:ery|ical|eon)|oncosurg|nutrition|dietetic|genetic|\beye\b|transplant|"
    r"\bortho\b|anatom|palliative|critical\s+care|\bicu\b|neonatolog|"
    r"experimental\s+medicine|virolog|oral\s+health|physiotherap|translational|"
    r"regenerative|\bcsic\b|central\s+sterile|medtech|biodesign|radi\w*\s*diagnosis|"
    r"opthal|audiolog|speech",
    re.I)

# "Dept. of X" / "Department of X": the title names its department. If X is not
# a pathology discipline, the post is someone else's, even when X is a
# speciality OTHER_DEPT does not list (or a misspelling of one).
DEPT_OF = re.compile(
    r"\b(?:dept\.?|deptt\.?|department)\s+of\s+([a-z][a-z&,.\-/ ]{2,60})", re.I)

# A medium (department-unconfirmed) title must say what is happening. Without
# one it is a navigation label ("Senior Residents", "Fellowships",
# "Junior Residents and Senior Residents") rather than a notice.
ACTION = re.compile(
    r"recruit|advert|\badvt|walk[\s-]*in|interview|vacan|\bposts?\b|engagement|"
    r"applications?|invited|filling|notice|notification|corrigendum|addendum|"
    r"extension|last\s+date|schedule|exam|hiring|appointment\s+(?:to|of|on)\s+the\s+post|"
    r"required|wanted",
    re.I)

# Never a vacancy for this user, whatever else the text says.
DROP = re.compile(
    r"technician|technologist|lab(?:oratory)?\s+(?:tech|assistant|attendant)|"
    r"\bnurse|nursing\s+officer|\bjrf\b|\bsrf\b|junior\s+research|senior\s+research\s+fellow|"
    r"research\s+(?:associate|assistant|scientist|officer|coordinator|fellow)|"
    r"\bproject\b|scientist|data\s+entry|\bdeo\b|multi[\s-]*tasking|\bmts\b|"
    r"\bintern(?:ship)?s?\b|student|admission|prospectus|counsel+ing|\bmbbs\b|"
    r"\bmd\s*/\s*ms\b|\bph\.?\s?d\b|"
    r"result|selection\s+list|selected\s+candidate|merit|short[\s-]*list|"
    r"eligib|admit\s+card|roster|recruitment\s+rules|\brrs?\b|seniority|promotion|"
    r"tender|quotation|orientation|cancel+ation|\bcancel|refund|"
    # lifecycle documents that come AFTER the vacancy has closed
    r"selection\s+(?:order|of|as)|\bselected\b|appointment\s+order|offer\s+of\s+appointment|"
    r"rejected|joining|undertaking|uploading\s+of\s+marks|\bmarks\b|waiting\s+list|"
    r"candidates?\s+(?:are\s+)?(?:permitted|allowed)|leave\s+form|"
    r"clinical\s+associate|alumni|travel+ing|observership|"
    r"\bsr\.?\s+(?:medical|administrative|nursing|technical|scientific|assistant|clerk|accounts)|"
    r"administrative\s+officer|non[\s-]*medical|stipend|salary|applications?\s+received|"
    r"\breader\b|\bcourses?\b|^\s*home\b",
    re.I)

# Mojibake: UTF-8 bytes decoded as Latin-1 / cp1252. Devanagari is 3 bytes per
# character, so each lead byte (à = 0xE0) carries one or two continuation bytes.
MOJIBAKE = re.compile(
    r"(?:[À-ï][\u0080-¿‘-›Œ-Ÿˆ˜]{1,2}){2,}")

_YEAR = re.compile(r"(?<!\d)(20\d\d)(?!\d)")


def _flat(text):
    return re.sub(r"\s+", " ", text or "").strip()


def judge(title, href=""):
    """Classify a link from its visible title and its href (or a stored
    listing's title and url). The href can carry the post or the department
    ("/sr-pathology-advt.pdf"), but only the title can show that a link is a
    real notice rather than a navigation label."""
    title = _flat(title)
    t = f"{title} {_flat(href)}".strip()
    if not t or OFF_SCOPE.search(t) or DROP.search(t) or not POST.search(t):
        return None
    if PATHOLOGY.search(t):
        return "high"
    if not ACTION.search(title):
        return None
    if MULTI_DEPT.search(t):
        return "medium"
    if names_other_department(title):
        return None
    # An SR / fellowship notice that names no department at all.
    return "medium"


def names_other_department(title):
    """True when the title names a department and it is not pathology."""
    t = _flat(title)
    if PATHOLOGY.search(t):
        return False
    if OTHER_DEPT.search(t):
        return True
    m = DEPT_OF.search(t)
    return bool(m) and not MULTI_DEPT.search(t)


def is_stale(title, today):
    """True when every year a title mentions is before this year.

    Recruitment pages keep years of archived notices ("Walk in interview ...
    on 12.09.2019"). A notice whose newest date is last year or older is not
    a vacancy you can apply to now. In January and February last year's
    notices still count, because a December advertisement is often open into
    the new year. A title with no year is not judged."""
    years = [int(y) for y in _YEAR.findall(title or "")]
    if not years:
        return False
    floor = today.year - (1 if today.month <= 2 else 0)
    return max(years) < floor


def judge_document(title, body):
    """Re-judge a candidate once its document body is known.

    The body decides the discipline: a document that names a pathology
    discipline confirms the seat; one that does not means the notice is for
    other departments. The post can come from the title or the opening of the
    body (a bare "Advertisement" PDF link says nothing on its own)."""
    t, b = _flat(title), _flat(body)
    if OFF_SCOPE.search(t) or DROP.search(t):
        return None
    # The body can confirm a pathology seat in a notice whose title is silent
    # about the department -- never overrule a title that names another one
    # (a "Fellow - Surgical Oncology" PDF that mentions pathology in passing).
    if not MULTI_DEPT.search(t) and names_other_department(t):
        return None
    if not POST.search(f"{t} {b[:4000]}"):
        return None
    return "high" if PATHOLOGY.search(b) else None


def mentions_pathology(text):
    t = _flat(text)
    return bool(t) and not OFF_SCOPE.search(t) and bool(PATHOLOGY.search(t))


def is_mojibake(text):
    return bool(MOJIBAKE.search(text or ""))
