"""Mechanical assumption notes for the cases built on an assumed API contract."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from tools.models import TestCase
from tools.standing_rules_text import (
    ASSUMED_MARKER,
    CLARIFY_MARKER,
    assumed_label,
    safe_ref,
)

logger = logging.getLogger("tools.standing_rules")


@dataclass(frozen=True)
class ContractBasis:
    """Whether the ticket carries a real API spec, and whether the label is bilingual."""

    has_spec: bool
    bilingual: bool = False


def _note_for(title: str, label: str, source_ref: str) -> str | None:
    """The Notes text for one upper-cased case title, or None when unmarked."""
    if ASSUMED_MARKER in title:
        return label
    if CLARIFY_MARKER in title:
        return (
            "Depends on an UNRESOLVED question in "
            f"{safe_ref(source_ref)} - confirm the intended behaviour "
            "before executing."
        )
    return None


# Detection is mechanical: the case title carries the ``[ASSUMED]`` marker the
# prompt asked for. Nothing is mutated -- the caller stores the mapping on the
# suite so ``tools/xlsx_generator`` renders it in the existing Notes column.
# Returns {} when nothing is marked. Never raises.
def annotate_assumed_cases(
    cases: list[TestCase], source_ref: str, basis: ContractBasis
) -> dict[str, str]:
    """Map tc_id -> Notes text for every case built on an assumed contract."""
    notes: dict[str, str] = {}
    try:
        if not cases:
            return {}
        label = assumed_label(source_ref, basis.has_spec, basis.bilingual)
        for tc in cases:
            title = (getattr(tc, "title", "") or "").upper()
            note = _note_for(title, label, source_ref)
            if note is not None:
                notes[tc.tc_id] = note
    except Exception:
        logger.exception("annotate_assumed_cases failed - no notes attached")
        return {}
    return notes
