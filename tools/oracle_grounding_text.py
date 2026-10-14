"""Text normalisation and the case-own data text (leaf of oracle_grounding)."""

from __future__ import annotations

import logging
import re

from tools.models import TestCase

logger = logging.getLogger("tools.oracle_grounding")

_SMART_CHARS = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        " ": " ",
    }
)

# Joins the case's OWN data fields. It has to be a character that cannot occur
# inside a quoted span, because joining with whitespace lets a match straddle
# two unrelated fields: with a space, a case whose postcondition ended
# "... disabled" sitting next to a data value "online purchases" grounded the
# invented string 'Online purchases disabled' out of two halves that were never
# adjacent. Measured on the 2026-08-16 run: three true positives lost that way.
_FIELD_SEP = "\x00"


def _normalize(text: str | None) -> str:
    """Case-folded, whitespace-collapsed, quote-normalized text.

    Newlines collapse to spaces on purpose: a source description is wrapped
    prose, so a promise the generator quotes on one line is frequently written
    across two. The cost is that a span can be grounded by source text that
    straddles a line break, and the direction of that error is SILENCE -- the
    safe direction for an advisory that must not cry wolf.
    """
    try:
        collapsed = re.sub(r"\s+", " ", (text or "").translate(_SMART_CHARS))
        return collapsed.strip().casefold()
    except Exception:
        logger.exception("_normalize failed - treating the text as empty")
        return ""


def _case_data_text(tc: TestCase) -> str:
    """The case's own DATA fields, sentinel-joined.

    A case that seeds ``cardholder_id: CH-100452`` and then asserts the audit
    row shows 'CH-100452' has invented nothing -- it is checking that its own
    input reached the database. Only the DATA fields count: preconditions,
    actions and postconditions are written by the same generator as the
    assertion, so letting them ground it would let the model confirm itself.
    """
    parts: list[str] = []
    try:
        for step in getattr(tc, "steps", None) or []:
            parts.append(_normalize(getattr(step, "test_data", None)))
        for item in getattr(tc, "test_data", None) or []:
            parts.append(_normalize(str(getattr(item, "example_value", "") or "")))
    except Exception:
        logger.exception("_case_data_text failed - using what was collected")
    return _FIELD_SEP.join(parts)
