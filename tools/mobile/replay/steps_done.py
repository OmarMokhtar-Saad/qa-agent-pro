"""Replay engine: the ``done`` and ``ask_tester`` steps. Cut verbatim from tools/mobile/replay/steps.py (C5 w17)."""

from __future__ import annotations

from typing import Any

from tools.mobile.replay.context import (
    STATUS_DONE,
    STATUS_NEEDS_TESTER,
    STATUS_UNVERIFIED,
    ReplayStop,
    _ReplayRun,
    _result,
)
from tools.mobile.replay.guard import _asserts_new_element
from tools.mobile.replay.screen import _verified
from tools.mobile.replay.steps_stop import StepAt, _close, _reply, _stop

_UNVERIFIED_PASS = (
    "verdict=pass was not accepted: nothing in this run "
    "has recorded a positive assert_pass (text_present, element, "
    "new_text), so nothing was verified; text_absent alone only "
    "shows something is missing. Add an `assert` for what the case "
    "is supposed to show on screen. "
)
_BLOCKED_PASS = (
    "verdict=pass was not accepted: the previous turn failed "
    "to type or changed nothing on screen, and this turn "
    "asserts no new element. Add an `assert` of kind "
    "`element` or `new_text` for what the step produced. "
)


def _downgraded(run: _ReplayRun, verdict: str, reason: str) -> tuple[str, str]:
    """A ``pass`` nothing verified is downgraded to unverified, with why."""
    if verdict != "pass":
        return verdict, reason
    if not _verified(run.trace, run.ctx):
        return STATUS_UNVERIFIED, (_UNVERIFIED_PASS + reason).strip()
    if run.ctx.prior_turn_blocked and not _asserts_new_element(run.trace):
        return STATUS_UNVERIFIED, (_BLOCKED_PASS + reason).strip()
    return verdict, reason


def _done_stop(run: _ReplayRun, action: Any, at: StepAt) -> dict:
    """The reply for a ``done`` step. A ``pass`` nothing verified is downgraded."""
    verdict = str(getattr(action, "verdict", ""))
    reason = str(getattr(action, "reason", ""))[:400]
    verdict, reason = _downgraded(run, verdict, reason)
    at.entry.update(outcome="done", detail=reason)
    _close(run, at)
    stop = ReplayStop(STATUS_DONE, run.trace, at.screen, verdict, reason, at.position)
    return _reply(_result(stop))


def _ask_tester_step(run: _ReplayRun, action: Any, at: StepAt) -> dict | None:
    """An ``ask_tester`` step: the reply that asks, or ``None`` when the tester
    already supplied the field and the replay goes on."""
    field = str(getattr(action, "field", ""))
    supplied = (run.ctx.tester_inputs or {}).get(field)
    if supplied is None:
        prompt = str(getattr(action, "prompt", ""))[:300]
        at.entry.update(outcome="needs_tester", detail=prompt)
        _close(run, at)
        stop = _stop(run, at, STATUS_NEEDS_TESTER)
        return _reply(_result(stop, field=field))
    at.entry.update(outcome="supplied", detail="the tester supplied " + field)
    _close(run, at)
    return None
