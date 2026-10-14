"""Labelled free-prose values masked by the redaction nets."""

from __future__ import annotations

import re

from tools.mobile_shared.scrub_mask import mask_value

# ── free prose ──────────────────────────────────────────────────────────────────
#
# The key nets need a KEY. An SDK's prompt body carries the patient as prose --
# ``FACTS: patient: age 42, m, Riyadh, <name>, height 180cm, blood type B+`` and a
# dependents roster -- and reached disk and the page in cleartext on the first live
# capture. These are LABELLED-VALUE patterns, each bounded (no unbounded quantifier
# over the whole text), so they cannot run away over a multi-megabyte body.
_PROSE_AGE_RE = re.compile(
    r"(?i)(?P<label>\b(?:age|\u0639\u0645\u0631)\s*[:=]?\s*)(?P<value>\d{1,3})\b"
)
_PROSE_BLOOD_RE = re.compile(
    r"(?i)(?P<label>\bblood\s*type\s*[:=]?\s*)(?P<value>(?:AB|A|B|O)[+-])"
)
_PROSE_PHONE_RE = re.compile(r"(?<!\d)(?P<value>(?:\+?966|0)5\d{8})(?!\d)")
# The roster label, then up to six comma-separated tokens of at most 60 chars each.
_PROSE_PERSON_RE = re.compile(
    r"(?i)(?P<label>\b(?:patient|dependents?|\u0627\u0644\u0645\u0631\u064a\u0636|\u0627\u0644\u062a\u0627\u0628\u0639(?:\u064a\u0646)?)\s*:\s*)"
    r"(?P<list>[^,\n\u00b7]{1,60}(?:,\s*[^,\n\u00b7]{1,60}){0,5})"
)
# A token that is a bare number, a number with a unit, or a one-letter code is kept:
# ``180cm``, ``71kg``, ``m``, ``f`` say nothing about WHO.
_PROSE_KEEP_RE = re.compile(
    r"^(?:[a-z]|\d+(?:\.\d+)?\s*[a-z%]{0,6}|\w+\s+\d+(?:\.\d+)?\s*[a-z%]{0,6})$", re.I
)
# A token that carries a measurement label, or that an earlier pass already masked,
# is kept whole: ``age <redacted...>`` must not be masked a second time with its label.
_PROSE_LABELLED_RE = re.compile(
    r"^(?:age|blood\s*type|height|weight|bmi|bp|glucose|hr|spo2|temp|waistline|doses)\b|^<redacted:",
    re.I,
)


def _mask_list(match: re.Match) -> str:
    tokens = match.group("list").split(",")
    out = []
    for token in tokens:
        raw = token.strip()
        if not raw or _PROSE_KEEP_RE.match(raw) or _PROSE_LABELLED_RE.search(raw):
            out.append(token)
        else:
            lead = token[: len(token) - len(token.lstrip())]
            out.append(lead + mask_value(raw))
    return match.group("label") + ",".join(out)


def scrub_prose(text: str) -> str:
    """Mask the labelled personal values a prompt carries as prose.

    Age, blood type and a phone-shaped run are masked wherever they appear; the tokens
    after a ``patient:`` / ``dependents:`` label are masked unless they are a number, a
    measurement or a one-letter code. The LABEL always survives, so a reader sees that a
    value stood there. Anything not labelled is NOT touched -- stated in the docs.
    """
    if not text:
        return text
    out = _PROSE_AGE_RE.sub(
        lambda m: m.group("label") + mask_value(m.group("value")), text
    )
    out = _PROSE_BLOOD_RE.sub(
        lambda m: m.group("label") + mask_value(m.group("value")), out
    )
    out = _PROSE_PHONE_RE.sub(lambda m: mask_value(m.group("value")), out)
    return _PROSE_PERSON_RE.sub(_mask_list, out)
