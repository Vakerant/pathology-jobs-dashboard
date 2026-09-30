"""Regression tests for the revamped dashboard template.

The 2026-09 UI revamp put real logic into `templates/dashboard.html` -- a
recency ladder, an actionable-document channel, a URL scheme guard and a
humaniser for raw scraper exceptions. That logic needs a gate, because each of
the defects the revamp fixed came back silently once already:

  * a contrast failure    -- the old scrape-result text `#1f6b34` on a dark
                             surface measured 2.9:1, below the 4.5:1 floor.
  * an undefined token    -- the old `--muted` (the real token is `--mut`), so
                             the busy-state colour silently fell back to inherit.
  * a dead token          -- the old `--gold` was defined and never consumed.
  * hover-only reporting  -- the three failing sources explained themselves only
                             through a `title=` tooltip full of urllib3 noise.
  * a re-animating list   -- every card animated on every 60s poll, because the
                             animation was bound to `render()` and not to paint.
  * an unguarded href     -- scraped third-party HTML can carry a
                             `javascript:` URL into the card link.

Deliberately NOT asserted here: corpus counts. The live corpus drifts on every
scrape (listings, hidden rows and document-type tallies all move), so anything
count-based would turn a routine scrape into a red build. Where a count matters
it is derived from the data at test time.

Source-level invariants are checked in Python. Behaviour of the pure helpers is
checked by evaluating those exact source lines in Node, so the assertions run
against the shipped code rather than a paraphrase of it. The Node leg skips
cleanly when Node is absent.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "dashboard.html"
HTML = TEMPLATE.read_text(encoding="utf-8")

# ── WCAG helpers ────────────────────────────────────────────────────────


def _linear(channel: float) -> float:
    c = channel / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(hex_colour: str) -> float:
    r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def _contrast(fg: str, bg: str) -> float:
    a, b = _luminance(fg), _luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _root_block() -> str:
    m = re.search(r":root\{(.*?)\n\}", HTML, re.S)
    assert m, ":root block not found -- the whole token system hangs off it"
    return m.group(1)


def _defined() -> set[str]:
    """Every custom property declared in :root, whatever its value type."""
    return set(re.findall(r"(--[a-z0-9-]+)\s*:", _root_block()))


def _tokens() -> dict[str, str]:
    """Only the colour tokens -- the ones contrast can be computed from."""
    return dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})", _root_block()))


# ── token system coherence ──────────────────────────────────────────────


def test_every_referenced_custom_property_is_defined():
    """The `--muted` class of bug: a var() that resolves to nothing at all."""
    used = set(re.findall(r"var\(\s*(--[a-z0-9-]+)", HTML))
    assert not (used - _defined()), f"referenced but never defined: {sorted(used - _defined())}"


def test_no_dead_custom_properties():
    """The `--gold` class of bug: a token defined, documented, never consumed."""
    used = set(re.findall(r"var\(\s*(--[a-z0-9-]+)", HTML))
    assert not (_defined() - used), f"defined but never used: {sorted(_defined() - used)}"


def test_colour_literals_are_tokenised():
    """Off-scale hex values outside :root are how a one-off value sneaks in."""
    body = HTML.replace(_root_block(), "")
    body = re.sub(r"<svg\b.*?</svg>", "", body, flags=re.S)  # the brand mark is self-contained art
    body = re.sub(r"(?:-webkit-)?mask-image:[^;}]*", "", body)  # a mask stop is not a surface colour
    stray = re.findall(r"#[0-9a-fA-F]{3,8}\b", body)
    assert not stray, f"hex literals outside :root (route them through tokens): {sorted(set(stray))}"


def test_radius_and_spacing_use_the_shared_scale():
    """No ad-hoc pixel radii or margins -- one scale, consumed via tokens."""
    root = re.search(r":root\{.*?\n\}", HTML, re.S).group(0)
    body = HTML.replace(root, "")
    ad_hoc = {
        v
        for decl in re.findall(r"(?:border-radius|margin|padding|gap)\s*:\s*([^;}]+)", body)
        for v in re.findall(r"(?<![-\w.])\b\d+(?:\.\d+)?px\b", decl)
    }
    # 1-3px are hairlines and focus offsets; 2/5/6px are optical nudges inside
    # chips, rows and the legend; 34px is the search field's icon gutter. All are
    # deliberate sub-scale adjustments -- layout spacing still uses --s-*.
    allowed = {"1px", "2px", "3px", "4px", "5px", "6px", "34px"}
    assert not (ad_hoc - allowed), f"off-scale spacing/radius: {sorted(ad_hoc - allowed)}"


# ── contrast (WCAG 2.2 AA) ──────────────────────────────────────────────

TEXT_PAIRS = [
    (ink, f"--{surface}")
    for ink in ("--ink", "--ink-soft", "--mut", "--faint", "--hemat")
    for surface in ("paper", "paper-2", "card", "card-2")
] + [(f"--{stain}-ink", f"--{stain}-tint") for stain in ("hemat", "eosin", "amber", "teal", "gold")]

NON_TEXT_PAIRS = [("--edge", f"--{s}") for s in ("paper", "paper-2", "card", "card-2")]


@pytest.mark.parametrize(("fg", "bg"), TEXT_PAIRS, ids=lambda v: v.strip("-"))
def test_text_tokens_meet_wcag_aa(fg, bg):
    """4.5:1 for body text. `--faint` is the dimmest token allowed by design."""
    tokens = _tokens()
    assert fg in tokens and bg in tokens, f"{fg}/{bg} not resolvable to a hex token"
    ratio = _contrast(tokens[fg], tokens[bg])
    assert ratio >= 4.5, f"{fg} on {bg} is {ratio:.2f}:1, below the 4.5:1 floor"


@pytest.mark.parametrize(("fg", "bg"), NON_TEXT_PAIRS, ids=lambda v: v.strip("-"))
def test_control_borders_meet_wcag_1_4_11(fg, bg):
    """3:1 for the borders of controls and meaningful icons (WCAG 1.4.11)."""
    tokens = _tokens()
    ratio = _contrast(tokens[fg], tokens[bg])
    assert ratio >= 3.0, f"{fg} on {bg} is {ratio:.2f}:1, below the 3:1 floor for control borders"


def test_focus_ring_is_visible_on_every_surface():
    tokens = _tokens()
    for surface in ("paper", "paper-2", "card", "card-2"):
        ratio = _contrast(tokens["--hemat"], tokens[f"--{surface}"])
        assert ratio >= 3.0, f"focus ring on --{surface} is {ratio:.2f}:1"


def test_focus_indicator_is_never_removed():
    assert ":focus-visible" in HTML
    assert not re.search(r"outline\s*:\s*(none|0)\b", HTML), "outline removed without a replacement"
    assert not re.search(r"outline\s*:\s*(none|0)[^;}]*!important", HTML)


def test_reduced_motion_is_honoured():
    block = re.search(r"@media\s*\(prefers-reduced-motion\s*:\s*reduce\)\s*\{(.*?)\n\}", HTML, re.S)
    assert block, "no prefers-reduced-motion block"
    assert "animation" in block.group(1) and "transition" in block.group(1)


# ── markup validity ─────────────────────────────────────────────────────


def test_no_duplicate_element_ids():
    ids = re.findall(r'\sid="([a-zA-Z][\w-]*)"', HTML)
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate id(s): {sorted(dupes)}"


def test_exactly_one_h1_and_ordered_headings():
    assert len(re.findall(r"<h1[\s>]", HTML)) == 1, "a document has exactly one h1"
    levels = [int(m) for m in re.findall(r"<h([1-6])[\s>]", HTML)]
    for prev, cur in zip(levels, levels[1:]):
        assert cur <= prev + 1, f"heading level jumps h{prev} -> h{cur}"


def test_landmarks_and_viewport_present():
    for tag in ("<header", "<main", "<footer", "<nav"):
        assert tag in HTML, f"missing {tag} landmark"
    assert 'name="viewport"' in HTML
    assert "user-scalable=no" not in HTML and "maximum-scale" not in HTML, "zoom is disabled"


def test_every_image_has_an_alt_and_links_are_safe():
    for tag in re.findall(r"<img[^>]*>", HTML):
        assert "alt=" in tag, f"img without alt: {tag[:60]}"
    for attrs in re.findall(r"<a\b[^>]*>", HTML):
        if 'target="_blank"' in attrs:
            assert 'rel="noopener' in attrs, f"target=_blank without noopener: {attrs[:70]}"


def test_filter_controls_are_real_buttons_with_pressed_state():
    assert "<button" in HTML
    for host in ("regionlist", "catlist", "doclist"):
        assert f'id="{host}"' in HTML, f"filter rail #{host} missing"
    assert 'aria-pressed' in HTML, "toggle filters must expose aria-pressed"
    assert 'type="button"' in HTML or "<button" in HTML


def test_result_region_is_labelled_and_live():
    assert 'id="results"' in HTML
    assert 'id="scrape-result"' in HTML
    assert 'aria-live="polite"' in HTML, "scrape feedback must announce itself to a screen reader"
    assert 'class="skip"' in HTML and 'href="#results"' in HTML


# ── the recency ladder: the redesign's central claim ────────────────────


def test_recency_ladder_is_driven_by_first_seen():
    """`first_seen` is 100% populated; `deadline` is on 14 rows, all in the past.

    A countdown built on `deadline` would render as dead data on 98.7% of cards,
    so the ladder must key off discovery time.
    """
    assert "l.first_seen" in HTML, "age ladder must read first_seen"
    labels = re.search(r"const TIER_LABEL = \[(.*?)\]", HTML)
    assert labels and len(labels.group(1).split(",")) == 4, "expected a 4-step ladder"
    prose = HTML[HTML.index("<body>"):]  # CSS `left:` is not urgency copy
    prose = re.sub(r"/\*.*?\*/", "", prose, flags=re.S)  # nor is a comment about it
    prose = re.sub(r"^\s*//.*$", "", prose, flags=re.M)
    for pattern in (r"days?\s+left", r"\bcountdown\b", r"\bhurry\b", r"\bclosing in\b", r"\bact now\b"):
        assert not re.search(pattern, prose, re.I), f"unearned urgency copy matched {pattern!r}"


def test_every_card_shows_when_it_was_discovered():
    """100% coverage claim: the ladder label renders unconditionally, not per-tier."""
    card = re.search(r"function card\(l\)\{(.*?)\n\}", HTML, re.S)
    assert card, "card() not found"
    body = card.group(1)
    assert "TIER_LABEL" in body, "card must render its ladder label"
    assert "first_seen" in body or "effDate" in body, "card must render a date"
    # A countdown is legitimate for the 14 rows that carry a real deadline, and
    # only there. What must never happen is an unconditional countdown.
    for use in re.findall(r".*daysUntil.*", body):
        assert re.search(r"l\.deadline\s*\?", use), f"daysUntil used unguarded: {use.strip()[:70]}"


# ── actionable documents ────────────────────────────────────────────────


def test_actionable_document_types_all_have_labels():
    docs = re.search(r"const ACTION_DOCS = \[(.*?)\]", HTML).group(1)
    types = re.findall(r"'([A-Z_]+)'", docs)
    labels = re.search(r"const DOC_LABELS = \{(.*?)\}", HTML, re.S).group(1)
    assert types, "ACTION_DOCS is empty"
    for t in types:
        assert f"{t}:" in labels, f"{t} is filterable but has no human label"


def test_actionable_set_excludes_bulk_document_types():
    """UNKNOWN + ADVERTISEMENT are 904 of 1067 rows. Labelling them is noise."""
    docs = re.search(r"const ACTION_DOCS = \[(.*?)\]", HTML).group(1)
    for bulk in ("UNKNOWN", "ADVERTISEMENT"):
        assert bulk not in docs, f"{bulk} is the bulk of the corpus, not an actionable signal"


def test_document_channel_drives_both_label_and_spine():
    assert re.search(r"data-doc=", HTML), "document type must key a visual channel"
    assert re.search(r"\.doc\[data-doc", HTML) or re.search(r"\[data-doc=", HTML)


# ── source health: failures must be visible, not hover-only ─────────────


def test_source_failures_are_rendered_not_hover_only():
    """The defect: 3 of 34 sources were failing and the reason was a tooltip."""
    assert "cleanErr" in HTML, "raw urllib3 exceptions must be humanised before display"
    assert re.search(r'class="err"', HTML), "no element to hold the failure reason"
    assert "HTTPSConnectionPool" not in HTML, "raw exception text is shipped to the browser"
    assert not re.search(r"\.src\b[^{]*\{[^}]*title\s*:", HTML), "reason must not be a tooltip"


def test_clean_err_names_the_fault_instead_of_the_stack():
    for pattern, cause in (
        (r"certificate|ssl", "certificate"),
        (r"403", "403"),
        (r"429", "429"),
        (r"timed? ?out|timeout", "timed out"),
        (r"404", "404"),
        (r"5\\d\\d", "server error"),
    ):
        assert re.search(pattern, HTML), f"cleanErr does not recognise {cause}"


# ── motion: paint once, not once a minute ───────────────────────────────


def test_card_animation_is_gated_on_first_paint():
    assert re.search(r"#results\.first-paint[^{]*\.card[^{]*\{[^}]*animation", HTML), (
        "card animation must be scoped to the first-paint class, otherwise every "
        "60s render() replays the whole list"
    )
    assert re.search(r"firstPaint", HTML) and "first-paint" in HTML


def test_stagger_schedule_terminates():
    """An unbounded per-child delay on 1000+ cards is minutes of empty screen."""
    assert re.search(r"nth-child\(n\+\d+\)\{[^}]*animation-delay", HTML), (
        "the stagger must collapse to a single capped delay after the first N cards"
    )


# ── behaviour of the pure helpers, run through Node ─────────────────────

NODE = shutil.which("node")

# Self-contained declarations lifted verbatim out of the page script. Each has
# no DOM dependency, so they can be evaluated outside a browser.
PURE_SNIPPETS = (
    re.escape("const $ = s => document.querySelector(s);"),
    r"const esc = s => .*?;\n",
    r"const safeUrl = u => .*?;\n",  # noqa: E501
    r"const ACTION_DOCS = \[.*?\];",
    r"const DOC_LABELS = \{.*?\};",
    r"const TIER_LABEL = \[.*?\];",
    r"const DAY = 86400000;",
    r"function cleanErr\(s\)\{.*?\n\}",
    r"function ageDays\(l, now\)\{.*?\n\}",
    r"function tierOf\(l\)\{.*?\n\}",
    r"function daysUntil\(iso\)\{.*?\n\}",
    r"function scrapeSummary\(s\)\{.*?\n\}",
)


def _extract_pure_js() -> str:
    parts = []
    for pattern in PURE_SNIPPETS:
        m = re.search(pattern, HTML, re.S)  # patterns are regexes; see PURE_SNIPPETS
        assert m, f"pure helper no longer matches {pattern!r} -- update PURE_SNIPPETS"
        parts.append(m.group(0))
    return "\n".join(parts)


def _run_node(body: str) -> str:
    """Evaluate the shipped helpers, then run `body` (an expression) against them."""
    assert NODE, "node disappeared between the skipif check and the call"
    src = _extract_pure_js() + "\n" + body
    res = subprocess.run(
        [NODE, "--input-type=module", "-e", src],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert res.returncode == 0, f"node failed:\n{res.stderr}"
    return res.stdout.strip()


def js(value) -> str:
    """A JS literal for a Python value -- repr() would emit `None`/`True`."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(value, ensure_ascii=False)


def _expr(expression: str) -> str:
    return _run_node(f"console.log(JSON.stringify({expression}))")


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_scraped_urls_cannot_inject_a_scheme():
    """Scraped third-party HTML is untrusted input; only http(s) may reach href."""
    for hostile in (
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        " javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "file:///etc/passwd",
        "/relative/path",
        "",
        None,
    ):
        assert _expr(f"safeUrl({js(hostile)})") == '"#"', f"safeUrl let {hostile!r} through"
    for good in ("https://a.example/x", "http://a.example/x", "HTTPS://A.EXAMPLE/X"):
        assert _expr(f"safeUrl({js(good)})") == json_str(good), f"safeUrl blocked {good!r}"


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_esc_neutralises_markup_in_untrusted_fields():
    payload = '<img src=x onerror="alert(1)"> & \'<b>\'</script>'
    out = json.loads(_expr(f"esc({js(payload)})"))
    # The five characters that could break out of an attribute or a text node
    # must all be entities. `onerror` surviving as plain text is inert.
    for entity in ("&lt;", "&gt;", "&quot;", "&#39;", "&amp;"):
        assert entity in out, f"esc did not emit {entity}: {out}"
    # Nothing may remain once the entities are taken back out -- that is what
    # "no character can escape the attribute or the text node" actually means.
    bare = out
    for entity in ("&lt;", "&gt;", "&quot;", "&#39;", "&amp;"):
        bare = bare.replace(entity, "")
    for ch in "<>\"'&":
        assert ch not in bare, f"esc left a bare {ch!r} in: {out}"
    assert "alert(1)" in out, "text content should survive, only delimiters are encoded"
    assert _expr("esc(null)") == '""' and _expr("esc(undefined)") == '""'


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_clean_err_maps_the_three_live_failures_to_plain_causes():
    """These are the real errors the current corpus produces, verbatim."""
    live = {
        "HTTPSConnectionPool(host='rmlh.mohfw.gov.in', port=443): Max retries exceeded "
        "with url: / (Caused by SSLCertVerificationError(unable to get local issuer certificate))": (
            "TLS certificate could not be verified"
        ),
        "HTTP 403": "blocked — HTTP 403, bot filter",
        "HTTPSConnectionPool(host='aiimsbhubaneswar.nic.in', port=443): Read timed out. "
        "(read timeout=30)": "connection timed out",
    }
    for raw, expected in live.items():
        got = _expr(f"cleanErr({raw!r})")
        assert got == json_str(expected), f"{raw[:40]!r} -> {got}"
        assert "urllib3" not in got and "HTTPSConnectionPool" not in got
    assert _expr("cleanErr('')") == '"no response from the source"'
    assert _expr("cleanErr('x'.repeat(200))").endswith('…"')


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_recency_ladder_boundaries():
    """Boundaries are <1d, <3d, <7d, older -- and the labels must line up."""
    day = 86_400_000
    cases = {0.0: 0, 0.5: 0, 1.0: 1, 2.9: 1, 3.0: 2, 6.9: 2, 7.0: 3, 400.0: 3}
    for days, tier in cases.items():
        ago = iso_ago(days)
        assert _expr(f"tierOf({{first_seen:{ago!r}}})") == str(tier), f"{days}d -> tier {tier}"
    assert _expr("tierOf({})") == "3", "a listing with no first_seen must not read as fresh"
    assert _expr("TIER_LABEL.length") == "4"


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_days_until_handles_missing_and_past_dates():
    assert _expr("daysUntil('')") == "null"
    assert _expr("daysUntil('not-a-date')") == "null"
    assert _expr("daysUntil('2020-01-01')") < "0", "a past deadline must be negative, not NaN"


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_scrape_summary_reports_every_signal():
    clean = _expr("scrapeSummary({last_run_new:2,total:1067,sources_ok:31,sources_failed:0,orphans:0,projection_ok:true})")
    assert "<b>2</b> new" in clean and "<b>1067</b> total" in clean and "<b>31</b> sources OK" in clean
    assert "failed" not in clean and "orphan" not in clean, "clean runs must not cry wolf"

    noisy = _expr("scrapeSummary({last_run_new:0,total:1067,sources_ok:31,sources_failed:3,orphans:0,projection_ok:true})")
    assert "<b>3</b> failed" in noisy

    drifted = _expr("scrapeSummary({last_run_new:0,total:10,sources_ok:9,sources_failed:1,orphans:0,projection_ok:false})")
    assert "projection drift" in drifted

    orphans = _expr("scrapeSummary({last_run_new:0,total:10,sources_ok:10,orphans:7})")
    assert "<b>7</b> projection orphans" in orphans

    bare = _expr("scrapeSummary(undefined)")
    assert "<b>0</b> new" in bare, "an absent stats object must not throw"


def json_str(value: str) -> str:
    """Node prints JSON, so compare against a JSON-encoded expectation."""
    return json.dumps(value, ensure_ascii=False)


def iso_ago(days: float) -> str:
    """A full ISO timestamp `days` in the past, shaped like `first_seen`."""
    when = datetime.now(timezone.utc) - timedelta(days=days)
    return when.isoformat()
