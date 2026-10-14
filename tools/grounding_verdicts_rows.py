"""Rows for the export's Assumed Requirements sheet (leaf of grounding_verdicts)."""

from __future__ import annotations

import logging

from tools.grounding_verdicts_types import Verdict, _by_id, _tc_id
from tools.models import TestCase

logger = logging.getLogger("tools.grounding_verdicts")

_ROW_HEADER = (
    "AR ID",
    "Submitted as",
    "Title",
    "Coverage Category",
    "Assumed requirement",
    "Why it is here",
)


def _row(index: int, tc: TestCase, notes: dict[str, Verdict]) -> list[str]:
    tc_id = _tc_id(tc)
    verdict = notes.get(tc_id)
    return [
        f"AR-{index:03d}",
        tc_id,
        getattr(tc, "title", "") or "",
        getattr(tc, "category", "") or "",
        (verdict.note if verdict else "") or "(not stated)",
        "Asserts behaviour the ticket does not state.",
    ]


def assumed_requirements_rows(
    routed: list[TestCase], verdicts: list[Verdict]
) -> list[list[str]]:
    """Header + one row per routed case, for the export's own sheet."""
    try:
        if not routed:
            return []
        notes = _by_id(verdicts)
        # Own id space. _finalize_generation renumbers the executable suite, so a
        # routed case that kept its TC-nnn would collide with an unrelated case
        # that inherited that number.
        rows = [list(_ROW_HEADER)]
        rows += [_row(index, tc, notes) for index, tc in enumerate(routed, 1)]
        return rows
    except Exception:
        logger.exception("assumed_requirements_rows failed - returning no rows")
        return []
