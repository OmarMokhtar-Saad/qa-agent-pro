"""Moved from handlers (refactor P2): _common."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

ProgressCb = Optional[Callable[[str], Awaitable[None]]]


# --- Guided-wizard choice callback (MCP elicitation bridge) ----------------- #
# mcp_server.py adapts FastMCP's ctx.elicit into this async callback, mirroring
# ProgressCb. It returns a ChoiceResult so these handlers stay importable and
# testable WITHOUT fastmcp and can react to accept / decline / (elicitation-)
# unavailable uniformly. A None callback (flag off / no client) is UNAVAILABLE.
CHOSEN = "chosen"
DECLINED = "declined"
UNAVAILABLE = "unavailable"


@dataclass
class ChoiceResult:
    """Outcome of one elicitation round. ``status`` is CHOSEN/DECLINED/UNAVAILABLE;
    ``value`` carries the selected option string when status is CHOSEN.

    2026-08-10 (K1): the two no-answer causes are recorded as FIELDS rather than as
    new ``status`` values, because ~20 call sites branch on
    ``status == UNAVAILABLE`` / ``== DECLINED`` and a new enum member would have
    changed all of them silently. Both default False, so every existing comparison
    is unaffected.

    ``timed_out``      -- the tester WAS asked and no answer arrived in time.
    ``budget_skipped`` -- the tester was NEVER asked: this call had already spent
                          its per-call elicitation budget on earlier dialogs.

    Keeping them apart matters because the messages differ: telling a tester "no
    answer arrived within 55s" when no dialog was ever shown is exactly the class of
    untrue tester-facing text this batch exists to remove."""

    status: str
    value: str | None = None
    timed_out: bool = False
    budget_skipped: bool = False


def unanswered(result: "ChoiceResult") -> bool:
    """True when no answer came back for a reason the CALLER must handle.

    One predicate on purpose: the two flags always travel together at a decision
    point and always diverge at a wording point, and pairing them by hand at each
    site is how the pair drifts. Sites that must word the cause read the flags
    directly; sites that only need "did we get an answer" read this."""
    return bool(result.timed_out or result.budget_skipped)


ChooseCb = Optional[Callable[[str, list], Awaitable["ChoiceResult"]]]
