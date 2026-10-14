"""Parse raw acceptance-criteria text into numbered AcceptanceCriterion items."""

from __future__ import annotations

import logging

from tools.rtm_sanitize import AcceptanceCriterion
from tools.rtm_split import _accept_ac_candidate, _split_ac_lines

logger = logging.getLogger("tools.rtm")


def _candidates(raw: str) -> list[str]:
    """The cleaned, non-empty criterion texts of ``raw``, in source order."""
    return [
        item
        for item in (_accept_ac_candidate(ln) for ln in _split_ac_lines(raw))
        if item
    ]


def _numbered(items: list[str]) -> list[AcceptanceCriterion]:
    """Number the criterion texts AC-001, AC-002, ..."""
    # NO count cap here, deliberately -- see the note above _AC_BLOCK_LIMIT.
    # Every criterion the ticket carries becomes an object, so the system
    # prompt, the job packet (`host_mode._prepared_ac_entries` reads these
    # same objects) and the coverage denominator are the same set, and no
    # reply can report coverage over a population it silently shrank.
    return [
        AcceptanceCriterion(ac_id=f"AC-{i:03d}", description=desc)
        for i, desc in enumerate(items, 1)
    ]


# Handles:
# - Bulleted lists (-, *, •)
# - Numbered lists (1., 2., 1), 2))
# - Plain prose separated by blank lines
# - Plain prose separated by single newlines
#
# Returns [] on empty input or any exception.
def parse_acceptance_criteria(raw: str) -> list[AcceptanceCriterion]:
    """Parse raw acceptance criteria text into numbered AcceptanceCriterion items."""
    try:
        if not raw or not raw.strip():
            return []
        items = _candidates(raw)
        if not items:
            return []
        return _numbered(items)
    except Exception:
        logger.exception("parse_acceptance_criteria failed — returning empty list")
        return []
