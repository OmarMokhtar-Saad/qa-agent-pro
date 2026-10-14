"""The exploratory lane's turn reply: fold a finding, a goal, or an extension into the state.

Cut from ``tools.mobile.explore_runner``; the facade re-exports ``apply_turn_result``
(identity). Never imports the facade.
"""

from __future__ import annotations

import logging

from tools.mobile import charter as charter_mod
from tools.mobile import finding_nag
from tools.mobile.explore_runner_consts import MAX_SUB_GOAL_CHARS, RUNNING
from tools.mobile.explore_runner_extend import _extension_notice
from tools.mobile.explore_runner_types import Recorded
from tools.mobile.explore_stop import (
    MAX_TURNS,
    STOP_FINDING,
    STOP_GOAL,
    stop_reason,
)

logger = logging.getLogger("tools.mobile.explore_runner")


def _stop_result(body: dict, stop: str, notice: str) -> dict:
    body["stop"] = stop
    return {"error": None, "content": {"state": body, "status": stop, "notice": notice}}


# The result for a turn that ends the run now (goal reached, or first finding).
#
# ``None`` when the turn does not end it; *body* gets ``stop`` only when it does.
#
# THE CHARTER'S STOP CONDITION, deliberately BELOW the
# ``goal_reached`` return and not above it. A turn may report BOTH
# a finding and ``goal_reached``; measured on the ordering this
# replaces, that run returned ``first_finding`` and ``stop_reason``
# then named the stop as ``first_finding`` for a run whose goal was
# actually REACHED -- a false statement about the run in the
# artifact whose purpose is reconstructing it. Goal-reached is the
# stronger and more informative outcome, and the finding is still
# recorded in ``body["findings"]`` either way, so precedence
# is expressed STRUCTURALLY by this order rather than by a second
# condition that could drift from the return above it. Ordering
# mutant M15 swaps the two blocks back;
# test_a_turn_that_reports_both_a_finding_and_the_goal_says_goal_reached
# is the pin that goes red.
#
# ``stop`` is the state's own existing stop field and
# ``stop_reason`` remains its ONLY reader, so this adds a writer
# and not a second derivation.
def _early_stop(body: dict, reply: dict, rec: Recorded) -> dict | None:
    """The result for a turn that ends the run now (goal reached, or first finding)."""
    if bool(reply.get("goal_reached")):
        return _stop_result(body, STOP_GOAL, rec.notice)
    if rec.finding and charter_mod.stop_on(body.get("charter")) == "first_finding":
        return _stop_result(body, STOP_FINDING, rec.notice)
    return None


def _nag_turn(body: dict, reply: dict) -> finding_nag.Turn:
    """The turn position the no-finding nudge is worded for."""
    return finding_nag.Turn(
        int(body.get("turn") or 0),
        int(body.get("turns_budget") or MAX_TURNS),
        final=bool(reply.get("goal_reached")),
    )


def _record_finding(body: dict, reply: dict) -> Recorded:
    """Store the reply's finding in *body*; return ``(finding, notice)``.

    ``notice`` is the no-finding nudge, empty when a finding was recorded.
    """
    finding = " ".join(str(reply.get("finding") or "").split())[:600]
    if finding:
        findings = list(body.get("findings") or [])
        findings.append({"turn": int(body.get("turn") or 0), "note": finding})
        body["findings"] = findings[:MAX_TURNS]
        return Recorded(finding, "")
    # Disclosed, never refused -- see NO_FINDING_NOTICE. An extension
    # verdict overwrites this later, on purpose: that one is about the
    # budget, which matters more than a nudge.
    return Recorded(finding, finding_nag.reminder(_nag_turn(body, reply)))


def _sub_goal(body: dict, reply: dict) -> None:
    # THE STEP IT IS ON NOW (fix round 3, item 1). The full goal goes to a
    # chat once per run; this short line rides every packet after it. A
    # reply that omits it keeps the last one.
    sub_goal = " ".join(str(reply.get("sub_goal") or "").split())
    if sub_goal:
        body["sub_goal"] = sub_goal[:MAX_SUB_GOAL_CHARS]


def _settle(body: dict, notice: str, now: float | None) -> dict:
    status = stop_reason(body, now=now) or RUNNING
    body["stop"] = status if status != RUNNING else ""
    return {
        "error": None,
        "content": {"state": body, "status": status, "notice": notice},
    }


# Fold a turn reply into the state: goal, a finding, or an extension.
#
# ``{"error", "content": {"state", "status", "notice"}}``. ``notice`` is the
# line a handler shows when an extension was refused -- refusing silently
# would read as the model's request having been granted.
def apply_turn_result(state: object, raw: object, *, now: float | None = None) -> dict:
    """Fold a turn reply into the state: goal, a finding, or an extension."""
    try:
        body = dict(state if isinstance(state, dict) else {})
        reply = raw if isinstance(raw, dict) else {}
        recorded = _record_finding(body, reply)
        _sub_goal(body, reply)
        stopped = _early_stop(body, reply, recorded)
        if stopped is not None:
            return stopped
        notice = recorded.notice
        if reply.get("request_extension"):
            notice = _extension_notice(body, reply, now)
        return _settle(body, notice, now)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.explore_runner.apply_turn_result failed")
        return {"error": str(exc), "content": None}
