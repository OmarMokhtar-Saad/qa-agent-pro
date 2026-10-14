"""Enum-value check: flag test cases that select an option the ticket never defines."""

from __future__ import annotations

import logging
import re

from tools import ru_patterns as rp
from tools.models import TestCase
from tools.ru_consts import _ENUM_FIELD_HINTS, _NON_LABEL_FIELD_FRAGMENTS

logger = logging.getLogger("tools.requirement_units")


def _case_text(tc: TestCase) -> str:
    """Title + preconditions + every step's action/data/expected, as one string."""
    chunks: list[str] = [
        getattr(tc, "title", "") or "",
        getattr(tc, "preconditions", "") or "",
    ]
    for step in getattr(tc, "steps", None) or []:
        chunks.append(getattr(step, "action", "") or "")
        chunks.append(getattr(step, "test_data", "") or "")
        chunks.append(getattr(step, "expected_result", "") or "")
    for item in getattr(tc, "test_data", None) or []:
        chunks.append(getattr(item, "notes", "") or "")
        # example_value too: a case whose only mention of the free-text escape
        # lives in the value itself was still being flagged.
        chunks.append(getattr(item, "example_value", "") or "")
    return " ".join(chunks)


def _normalize_label(text: str) -> str:
    return rp.WS_RE.sub(" ", (text or "").strip().strip("<>").strip().lower())


def _is_enum_field(field: str) -> bool:
    """True for an enum-ish field name that is not a non-label fragment."""
    if not any(hint in field for hint in _ENUM_FIELD_HINTS):
        return False
    return not any(frag in field for frag in _NON_LABEL_FIELD_FRAGMENTS)


def _claims_label(normalized: str, allowed: set[str]) -> bool:
    """True when ``normalized`` claims a specific option label not in ``allowed``."""
    if not normalized or normalized in allowed:
        return False
    # A bare number/short token is a count or a selector, never an
    # option label the ticket was supposed to define.
    if len(normalized) < 4 or not re.search(r"[a-z؀-ۿ]", normalized):
        return False
    if normalized[0].isdigit():
        return False  # "2 checkboxes" is a count, not a label
    # A generic instruction ("first listed reason",
    # "first_checkbox_reason") points at whatever the build shows
    # rather than claiming a specific label. Underscores are folded
    # to spaces first: `\bfirst\b` does not match "first_checkbox"
    # because `_` is itself a word character.
    spaced = normalized.replace("_", " ").replace("-", " ")
    return not rp._GENERIC_POINTER_RE.search(spaced)


def _unknown_enum_hits(items: list, allowed: set[str]) -> list[tuple[str, str]]:
    """(field, value) for each test-data item selecting an undefined option."""
    hits: list[tuple[str, str]] = []
    for item in items:
        field = (getattr(item, "field", "") or "").lower()
        if not _is_enum_field(field):
            continue
        value = getattr(item, "example_value", "") or ""
        if _claims_label(_normalize_label(value), allowed):
            hits.append((field, value))
    return hits


def _case_hits(
    tc: TestCase, allowed: set[str], allow_free_text: bool
) -> list[tuple[str, str, str]]:
    """(tc_id, field, value) for one case; empty when the case is skipped."""
    items = getattr(tc, "test_data", None) or []
    if not items:
        return []
    if allow_free_text and rp._FREE_TEXT_EVIDENCE_RE.search(_case_text(tc)):
        return []
    tc_id = getattr(tc, "tc_id", "") or ""
    return [(tc_id, f, v) for f, v in _unknown_enum_hits(items, allowed)]


# (tc_id, field, value) for cases selecting an option the ticket never defines.
#
# A case is flagged only when ALL of these hold, which keeps the check free of
# the false positives that would make it useless:
#
#   1. it carries a ``test_data`` item whose field name is enum-ish
#      (``cancel_reason``, ``reason``, ...);
#   2. that item's ``example_value`` is not one of the ticket's defined
#      labels; and
#   3. the case shows NO evidence of taking the free-text escape -- it never
#      mentions "Other reason" anywhere in its title, preconditions, steps or
#      notes. A case that legitimately types free text into Other reason
#      always names it, so it is spared.
#
# On the TICKET-5645 suite this is exactly the four unexecutable cases -- values
# "Delayed delivery", "Changed my mind" and "Wrong item" against a DF01 that
# defines three reasons -- while the case that genuinely uses Other reason
# free text is not flagged.
#
# Never raises.
def find_unknown_enum_values(
    cases: list[TestCase],
    enum_values: dict[str, set[str]],
    allow_free_text: bool = True,
) -> list[tuple[str, str, str]]:
    """(tc_id, field, value) for cases selecting an undefined option; never raises."""
    out: list[tuple[str, str, str]] = []
    try:
        if not cases or not enum_values:
            return []
        allowed = {_normalize_label(v) for vals in enum_values.values() for v in vals}
        if not allowed:
            return []
        for tc in cases:
            out.extend(_case_hits(tc, allowed, allow_free_text))
    except Exception:
        logger.exception("find_unknown_enum_values failed - returning what was found")
    return out
