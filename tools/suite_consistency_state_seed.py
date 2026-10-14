"""The state values a test case seeds, read from structured and inline test data."""

from __future__ import annotations

import logging
import re

from tools import suite_consistency_state_rx as _rx
from tools.models import TestCase

logger = logging.getLogger("tools.suite_consistency")


def _is_state_value(value: str) -> bool:
    """True when a value looks like a state label rather than a seeded id."""
    v = (value or "").strip().lower()
    if not v or len(v) > 40:
        return False
    # A seeded identifier ("ord-cancel-locked-001", "seed_order_shared_008").
    if re.fullmatch(r"[a-z0-9\-_]*\d{2,}[a-z0-9\-_]*", v):
        return False
    return bool(re.search(r"[a-z؀-ۿ]", v))


def _add_structured_states(tc: TestCase, out: set[str]) -> None:
    for item in getattr(tc, "test_data", None) or []:
        field = getattr(item, "field", "") or ""
        if not _rx.STATE_FIELD_RE.search(field):
            continue
        value = (getattr(item, "example_value", "") or "").strip().lower()
        if _is_state_value(value):
            out.add(value)


def _add_inline_states(tc: TestCase, out: set[str]) -> None:
    for step in getattr(tc, "steps", None) or []:
        blob = getattr(step, "test_data", None) or ""
        for key, value in _rx._INLINE_PAIR_RE.findall(blob):
            if not _rx.STATE_FIELD_RE.search(key):
                continue
            normalized = value.strip().lower()
            if _is_state_value(normalized):
                out.add(normalized)


def _seeded_states(tc: TestCase) -> set[str]:
    """Normalized state values this case seeds.

    Reads BOTH the structured ``test_data`` items and each step's free-text
    ``test_data`` string: generators put the seeded status in either place, and
    reading only the structured items missed every case that wrote
    "initial_status: Processing" into a step.
    """
    out: set[str] = set()
    try:
        _add_structured_states(tc, out)
        _add_inline_states(tc, out)
    except Exception:
        logger.exception("_seeded_states failed - returning what was found")
    return out
