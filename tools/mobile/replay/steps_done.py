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
from tools.mobile.replay.stop_shot import defer_shot

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


_WEAK_PASS = (
    " Note: this pass is weakly backed -- it rests on %s. Check it "
    "against what the case claims before relying on it."
)


def _typed_texts(trace: list[dict]) -> list[str]:
    """The text of every ``type``/``fill`` step recorded in *trace*."""
    found = []
    for item in trace:
        action = item.get("action") if isinstance(item, dict) else None
        if isinstance(action, dict) and action.get("op") in ("type", "fill"):
            found.append(str(action.get("text") or ""))
    return [text for text in found if text]


_MIN_TYPED = 3


def _names_typed(want: str, typed: str) -> bool:
    """Whether asserted text *want* is the typed value, or contains all of it.

    A short typed value ("1") would match any text holding it, so containment
    needs ``_MIN_TYPED`` characters.
    """
    return want == typed or (len(typed) >= _MIN_TYPED and typed in want)


def _asserts_typed_text(trace: list[dict]) -> bool:
    """True when a passed ``text_present`` names text this trace typed.

    The turn then proves its own typing, so an earlier turn's failure to type
    must not demote it.
    """
    typed = _typed_texts(trace)
    for item in trace:
        action = item.get("action") if isinstance(item, dict) else None
        if not isinstance(action, dict) or item.get("outcome") != "assert_pass":
            continue
        want = str(action.get("text") or "")
        if action.get("kind") == "text_present" and want:
            if any(_names_typed(want, text) for text in typed):
                return True
    return False


def _weak_basis(trace: list[dict]) -> str:
    """What a pass rests on when that is thin, else an empty string."""
    kinds = [
        str((i.get("action") or {}).get("kind") or "")
        for i in trace
        if isinstance(i, dict) and i.get("outcome") == "assert_pass"
    ]
    if kinds and all(kind == "text_absent" for kind in kinds):
        return (
            "text_absent only (something is missing, not that the right screen shows)"
        )
    return ""


def _downgraded(run: _ReplayRun, verdict: str, reason: str) -> tuple[str, str]:
    """A ``pass`` nothing verified is downgraded to unverified, with why."""
    if verdict != "pass":
        return verdict, reason
    if not _verified(run.trace, run.ctx):
        return STATUS_UNVERIFIED, (_UNVERIFIED_PASS + reason).strip()
    blocked = run.ctx.prior_turn_blocked
    if blocked and not (
        _asserts_new_element(run.trace) or _asserts_typed_text(run.trace)
    ):
        return STATUS_UNVERIFIED, (_BLOCKED_PASS + reason).strip()
    weak = _weak_basis(run.trace)
    if weak:
        reason = (reason + _WEAK_PASS % weak).strip()
    return verdict, reason


async def _done_stop(run: _ReplayRun, action: Any, at: StepAt) -> dict:
    """The reply for a ``done`` step. A ``pass`` nothing verified is downgraded."""
    verdict = str(getattr(action, "verdict", ""))
    reason = str(getattr(action, "reason", ""))[:400]
    verdict, reason = _downgraded(run, verdict, reason)
    at.entry.update(outcome="done", detail=reason)
    _close(run, at)
    defer_shot(run, at.screen)
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
