"""Replay engine: the ``assert`` step. Cut verbatim from tools/mobile/replay/steps.py (C5 w17)."""

from __future__ import annotations

from typing import NamedTuple

from tools.mobile.replay.context import (
    STATUS_NEEDS_MODEL,
    _ReplayRun,
    _result,
    _Step,
)
from tools.mobile.replay.steps_stop import StepAt, _close, _reply, _stop
from tools.mobile.replay.stop_shot import defer_shot
from tools.mobile.replay.verdicts import AssertView, _evaluate_assert
from tools.mobile.replay.verdicts_consts import MAX_VISUAL_DETAIL_CHARS


class Seen(NamedTuple):
    """The live screen and the screen content before the last screen-changing action."""

    screen: dict | None
    baseline_texts: set | None


# Nothing here can judge a picture. Stop and hand the check back to the
# model; the report gets ONE PNG of THIS screen, no reply carries it.
# `Script` already refused a visual that is not last.
def _visual_check(run: _ReplayRun, step: _Step, at: StepAt) -> dict:
    """The reply that stops on a ``visual`` assert."""
    note = str(getattr(step.action, "note", ""))
    detail = ("Visual check: " + note)[:MAX_VISUAL_DETAIL_CHARS]
    step.entry.update(outcome="visual_check", detail=detail)
    _close(run, at)
    stop = _stop(run, at, STATUS_NEEDS_MODEL)
    return _reply(_result(stop, visual_check=True))


async def _assert_step(run: _ReplayRun, step: _Step, seen: Seen) -> dict | None:
    """An ``assert`` step: ``None`` when it passed and the replay goes on, else
    the reply that stops it (a failed assert, or a visual check)."""
    at = StepAt(step.entry["index"], step.entry, seen.screen)
    if str(getattr(step.action, "kind", "")) == "visual":
        return _visual_check(run, step, at)
    view = AssertView(seen.screen, run.trace, seen.baseline_texts)
    ok, detail = _evaluate_assert(step.action, view)
    step.entry.update(outcome="assert_pass" if ok else "assert_fail", detail=detail)
    _close(run, at)
    if ok:
        return None
    defer_shot(run, at.screen)
    return _reply(_result(_stop(run, at, STATUS_NEEDS_MODEL)))
