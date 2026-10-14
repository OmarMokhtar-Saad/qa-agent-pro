"""Status callback plumbing and the two retired feature seams."""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

logger = logging.getLogger("agents.test_scenario_agent")


# What a best-effort step (status callback, private-attr attach, log line,
# disclosure text) can raise; anything else is a real bug and must surface.
_BEST_EFFORT_ERRORS = (
    AttributeError,
    KeyError,
    LookupError,
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
)


async def _emit_status(
    on_status: Callable[[str], Awaitable[None]] | None, message: str
) -> None:
    """Send a user-facing workflow status line (create → review → fix → finalize).

    Best-effort — a failing/absent callback never disrupts generation.
    """
    if on_status is None:
        return
    try:
        await on_status(message)
    except _BEST_EFFORT_ERRORS:
        logger.debug("on_status callback failed for %r", message, exc_info=True)


def checklist_remediation_enabled() -> bool:
    """Checklist-driven remediation. HARDCODED OFF since 2026-08-14.

    NOT settings-derived: QA_CHECKLIST_REMEDIATION_ENABLED was DELETED
    (flag-surface reduction, batch 8b) and hardcoded to its own code default.

    2026-08-16 (dead-code deletion P2-E1): the bounded critic/regeneration loop
    this seam used to switch -- ``_remediate_gaps`` and the critic pair -- was
    DELETED, so inside THIS module the seam now governs nothing. Its own
    HOST-side reader (handlers' GAP ROUND block) was deleted too, so
    this function has no remaining reader; retained for revival rather than
    deleted outright.
    """
    return False


def semantic_dedup_enabled() -> bool:
    """Always False -- there is no intra-suite semantic dedup.

    QA_SEMANTIC_DEDUP_ENABLED was DELETED and hardcoded OFF; nothing in the
    pipeline merges cases on similarity. NOT
    settings-derived.
    """
    return False
