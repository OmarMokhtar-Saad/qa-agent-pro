"""Criterion label patterns (ACn:/BRnn:) and label normalisation."""

from __future__ import annotations

import re

from config.settings_guard import never_raise

# `AC1:` / `AC-2.` / `AC 03)` -- the label form acceptance criteria are most
# often written in, and the one the split below used to miss ENTIRELY. A ticket
# whose criteria were written `AC1:`..`AC10:` followed by a trailing `Notes:`
# paragraph parsed as ONE criterion plus the Notes line, and the RTM then
# reported "2/2 acceptance criteria traced, all covered" over a ten-criterion
# ticket: a silent under-count that fails GREEN (F2, live run 2026-08-30).
#
# The trailing paragraph is what disabled the recovery path: the single-newline
# fallback fired only when the paragraph split produced <= 1 chunk, so any
# trailing prose -- Notes, links, a sign-off -- silently switched it off.
# 2026-08-31 (F3): the label was AC-ONLY. A ticket whose criteria are written
# `BR01:`..`BR14:` -- inline, on ONE line, which is exactly what a Business
# Rules table flattens to -- matched nothing, split nothing, and produced ONE
# criterion. The traceability sheet then read "0/1 traced, 66 orphans" over a
# fourteen-rule ticket (measured, TICKET-10051). The 2026-08-30 fix for this
# failure was written for the `ACn` INSTANCE; this is the class.
#
# Two guards keep the wider label safe: an inline split is only taken when at
# least two labels are present (so a lone "BR01" quoted in prose cannot shatter
# a paragraph), and only an `ACn` marker is STRIPPED from the resulting text --
# a `BR07:` prefix is deliberately KEPT so _trace_map can match a case tagged
# "BR07" back to the criterion it names.
_LABEL_BODY = r"[A-Z]{2,5}\s*-?\s*\d{1,3}\s*[:.)\]-]\s+"
_AC_LABEL_RE = re.compile(rf"(?m)^\s*{_LABEL_BODY}")
_ANY_LABEL_RE = re.compile(_LABEL_BODY)
_INLINE_LABEL_SPLIT_RE = re.compile(rf"(?=\b{_LABEL_BODY})")
_MIN_INLINE_LABELS = 2
_AC_SPLIT_RE = (
    r"(?m)(?:^\s*[-*•]\s+|^\s*\d+[.)\]]\s+"
    rf"|^\s*{_LABEL_BODY})|\n{{2,}}"
)

# Section labels that introduce prose ABOUT the ticket rather than a criterion.
# Admitting one creates a bogus AC that every downstream anchoring check then
# treats as ground truth -- the same class the numeric/date guard above exists
# for, reached through a different door.
_NON_AC_PREFIX_RE = re.compile(
    r"^\s*(notes?|links?|references?|out of scope|scope|assumptions?|context)\s*:",
    re.IGNORECASE,
)


# 2026-08-31 (F3): splitting a Business-Rules table inline leaves the table's
# own heading as the first chunk ("Business Rules:" with nothing after it). It
# is a section label, not a criterion. Matched ONLY when the line is the bare
# heading: a UC-table row reading "Business Rules: BR02: not all products have
# a cancelation service" carries a real requirement after the colon and must
# survive -- an earlier revision of this fix dropped it and cost a criterion.
_BARE_SECTION_RE = re.compile(
    r"^\s*(business rules?|acceptance criteria|rules?)\s*:?\s*$", re.IGNORECASE
)


def _norm_label(raw: object) -> str:
    """Canonicalise a non-AC criterion label (BR07, br-7, MSG01) or "".

    Mirrors ``normalize_ac_id``'s zero-padding for any short uppercase prefix,
    so "BR-7" and "BR07" compare equal. Returns "" for anything that is not
    label-shaped, which keeps free text out of the trace index. Never raises.
    """
    with never_raise():
        s = str(raw or "").strip().upper().replace(" ", "")
        m = re.match(r"^([A-Z]{2,5})[-_]?0*(\d{1,3})$", s)
        return f"{m.group(1)}-{int(m.group(2)):03d}" if m else ""
    return ""


def _leading_label(text: object) -> str:
    """The normalised label a criterion's own text starts with, or "".

    ``BR07: System shall ...`` -> ``BR-007``. Never raises.
    """
    with never_raise():
        m = _ANY_LABEL_RE.match(str(text or "").strip())
        return _norm_label(re.sub(r"[\s:.)\]-]+$", "", m.group(0))) if m else ""
    return ""
