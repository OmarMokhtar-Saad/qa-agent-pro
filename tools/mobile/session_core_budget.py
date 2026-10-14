"""Mobile session core: the per-call budget and the bounded-call reply. Cut verbatim from tools/mobile/session_core.py (C5 w17 sessA)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from tools.mobile.session_core_state import CALL_BUDGET_S, PACKET_RESERVE_S, STATE_BUSY


@dataclass(frozen=True)
class Budget:
    """A monotonic deadline for ONE tool call.

    A value object, so a test constructs an ALREADY-EXPIRED budget instead of
    waiting for one -- which is what keeps the early-return tests structural.
    """

    deadline: float

    def remaining(self) -> float:
        return max(0.0, float(self.deadline) - time.monotonic())

    def expired(self) -> bool:
        return time.monotonic() >= float(self.deadline)


def new_budget(seconds: float = CALL_BUDGET_S) -> Budget:
    """A budget starting now, never longer than :data:`CALL_BUDGET_S`."""
    span = min(float(seconds or CALL_BUDGET_S), float(CALL_BUDGET_S))
    return Budget(deadline=time.monotonic() + max(1.0, span))


# The bounded-call reply: usually a pointer, and never half-done.
#
# *packet* is the reserved-tail capture from :func:`_final_packet` -- a
# best-effort screen, or ``None`` when the device did not answer inside
# :data:`PACKET_RESERVE_S`. Passing it through here, rather than building it
# at the call site, keeps every caller's shape the same.
def _busy(resolved: object, tc_id: str, *, packet: object = None) -> dict:
    """The bounded-call reply: usually a pointer, and never half-done."""
    return {
        "error": None,
        "content": {
            "state": STATE_BUSY,
            "packet": packet,
            "tc_id": str(tc_id or ""),
            "resolved": resolved if isinstance(resolved, dict) else {},
        },
    }


def _in_reserve(budget: Any) -> bool:
    """Expired, or inside the reserved tail. ``expired()`` is asked first so a
    budget without ``remaining()`` (a duck-typed test budget) still works."""
    if budget.expired():
        return True
    remaining = getattr(budget, "remaining", None)
    return callable(remaining) and remaining() <= PACKET_RESERVE_S
