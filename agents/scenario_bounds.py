"""Per-category case-count bounds from feature complexity."""

from __future__ import annotations

import re

_COMPLEXITY_CONNECTIVE_RE = re.compile(
    r"\b(and|or|when|if|then|else|unless|with|via|per)\b", re.I
)


_COMPLEXITY_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")


# Domain/technical tokens: mixed-case (OAuth/PKCE), digit-bearing, or hyphenated
# compounds (refresh-token) — these signal real, testable machinery in few chars.
_COMPLEXITY_TECH_RE = re.compile(
    r"\b[A-Za-z0-9]*(?:[a-z][A-Z]|[A-Z]{2}|[0-9]|-)[A-Za-z0-9-]*\b"
)


_COMPLEXITY_AC_RE = re.compile(
    r"acceptance criteria|\bAC-?\d|requirement|\bgiven\b|\bshould\b|\bmust\b", re.I
)


def _text_signal(text: str) -> int:
    distinct_words = {w.lower() for w in _COMPLEXITY_WORD_RE.findall(text)}
    # Distinct vocabulary: denser wording -> more distinct nouns/fields to test.
    score = len(distinct_words) // 2
    # Technical/domain tokens (OAuth, PKCE, refresh-token, SHA-256) pack a lot of
    # testable behaviour into few characters.
    score += min(len({m.lower() for m in _COMPLEXITY_TECH_RE.findall(text)}), 6)
    # Connectives imply branching / multi-condition behaviour.
    score += len(_COMPLEXITY_CONNECTIVE_RE.findall(text))
    # Explicit AC/requirement framing implies real, testable structure.
    if _COMPLEXITY_AC_RE.search(text):
        score += 3
    return score


def _line_signal(text: str) -> int:
    # Multi-line / bulleted specs describe more behaviour per char.
    lines = [ln for ln in text.splitlines() if ln.strip()]
    score = len(lines) if len(lines) > 1 else 0
    return score + sum(1 for ln in lines if ln.lstrip()[:2] in ("- ", "* ", "• "))


def _ui_field_signal(ui_content: dict | None) -> int:
    # Live UI structure: each form field is another thing to test.
    if ui_content and not ui_content.get("error"):
        fields = (ui_content.get("ui_elements") or {}).get("form_fields") or []
        return len(fields)
    return 0


# A cheap signal count approximating feature complexity, independent of raw
# length (NB-013).
#
# Length alone under-rates a terse-but-dense feature (e.g. "OAuth PKCE
# refresh-token rotation with device binding" — few chars, but many distinct
# technical nouns and an implied multi-step flow). We combine several cheap
# signals: distinct-word count, technical/domain tokens, connective count
# (and/or/when/if/with...), AC/requirement wording, bullet/line count, and the
# number of UI form fields when live UI structure is present. All cheap regex
# counts — no LLM call.
def _complexity_signal_score(feature_text: str, ui_content: dict | None = None) -> int:
    """Cheap regex signal count approximating feature complexity (NB-013)."""
    text = feature_text or ""
    return _text_signal(text) + _line_signal(text) + _ui_field_signal(ui_content)


def _band_index(value: int, cuts: tuple[int, int], *, strict: bool) -> int:
    """Band 0/1/2 of *value* against the two ascending cut points.

    strict=True means a band starts ABOVE its cut (length > cut); False means
    AT the cut (signal >= cut).
    """
    return sum(value > c if strict else value >= c for c in cuts)


# Derive (min_count, max_count) from the FEATURE description as a complexity
# proxy (I-030, refined by NB-013).
#
# Must be measured on the feature text alone — not the fully-assembled prompt,
# which is inflated by RAG/Jira/web context and would push a trivial feature
# into the highest case-count band, wasting tokens on the whole 8-category
# fan-out.
#
# Length is a weak proxy on its own: a 40-char dense spec ("OAuth PKCE
# refresh-token rotation with device binding") is more complex than a 400-char
# lorem-ipsum. So we blend raw length with a cheap signal score (distinct
# nouns/fields, connectives, AC blocks, bullet/line count) and band on the max
# of the two. Bounds stay within the same (8,10)/(10,13)/(12,15) caps.
#
# A short-but-signal-rich feature can lift out of the smallest band even at
# low length.
#
# F10 (2026-08-30) -- WHY THERE IS STILL NO FLOOR BAND HERE, so the next
# reader does not re-add one. The finding is real: "make the reports better"
# (four words, no product, no screen, no behaviour) produces a payload
# asking for 8-10 cases per category, 64 overall, under the standard "an
# empty category is always wrong" instruction -- a direct instruction to
# invent, and the ambiguity gate only catches it at finalize, after the
# whole generation cost is paid. A `(2, 4)` band gated on `length < 40 and
# signal <= 2` was written, MEASURED, and reverted: "Login page" is 10
# chars with the same low signal and lands in it, and that input is pinned
# at (8, 10) deliberately -- a tester who names a real product surface wants
# a real suite. Length and this signal score cannot separate the two, so
# tuning the COUNT here trades a documented over-generation for an
# undocumented under-generation. What was fixed instead is the missing
# caveat: host_category_jobs._thin_source adds a do-not-invent clause to
# worker_instructions for exactly this shape. A count-level fix needs a
# signal that reads whether the text names a SURFACE, which this one does
# not.
def _case_count_bounds(
    feature_text: str, ui_content: dict | None = None
) -> tuple[int, int]:
    """Derive (min_count, max_count) from the feature text's complexity."""
    len_band = _band_index(len(feature_text or ""), (300, 800), strict=True)
    sig_band = _band_index(
        _complexity_signal_score(feature_text, ui_content), (8, 18), strict=False
    )
    band = max(len_band, sig_band)
    return ((8, 10), (10, 13), (12, 15))[band]
