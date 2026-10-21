"""How a stopped run closes the turns still in flight: the truth, never "done".

A turn the run left ``needs_model``/``planning`` is closed by its LAST step
outcome: a wait that timed out, an app that was left, or a system dialog is
``failed`` with that reason; anything else the model never came back to is
``abandoned``. ``done`` is only ever written by the run's own done() verdict.
``ended_as`` keeps the status the step reply used.
"""

from __future__ import annotations

import time

#: Last-step outcomes that mean the case itself did not get through.
FAILING_OUTCOMES = (
    "wait_timeout",
    "left_app",
    "system_dialog",
    "assert_fail",
    "missing_element",
    "route_mismatch",
)
#: The statuses a stopped run must not leave on a turn.
IN_FLIGHT = ("needs_model", "planning")
ABANDONED_REASON = "the run ended before the model came back to this case"


def _last_outcome(body: dict) -> dict:
    trace = [t for t in list(body.get("trace") or []) if isinstance(t, dict)]
    return trace[-1] if trace else {}


def _reason(body: dict, last: dict) -> str:
    text = str(last.get("detail") or body.get("reason") or "")
    outcome = str(last.get("outcome") or "")
    return " ".join((outcome + ": " + text if text else outcome).split())[:300]


def closed_status(body: dict) -> tuple[str, str]:
    """``(status, reason)`` a turn still in flight is closed as."""
    last = _last_outcome(body)
    in_progress = bool(last.get("in_progress"))
    if str(last.get("outcome") or "") in FAILING_OUTCOMES and not in_progress:
        return "failed", _reason(body, last)
    return "abandoned", str(body.get("reason") or "") or ABANDONED_REASON


def finalized(body: dict) -> dict | None:
    """The turn *body* closed out, or ``None`` when it needs no change."""
    closed_from = str(body.get("status") or "")
    if closed_from not in IN_FLIGHT:
        return None
    status, reason = closed_status(body)
    return {
        **body,
        "status": status,
        "reason": reason,
        "ended_as": closed_from,
        "updated": time.time(),
    }
