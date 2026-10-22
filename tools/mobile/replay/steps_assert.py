"""Replay engine: the ``assert`` step. Cut verbatim from tools/mobile/replay/steps.py (C5 w17)."""

from __future__ import annotations

from typing import NamedTuple

from tools.mobile.replay.assert_settle import recheck_after_wait
from tools.mobile.replay.context import (
    STATUS_NEEDS_MODEL,
    _ReplayRun,
    _result,
    _Step,
)
from tools.mobile.replay.screen import _screen_package
from tools.mobile.replay.screen_pkg import LEAVES_ON_PURPOSE
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


def _left_on_purpose(trace: list[dict]) -> bool:
    """True when an earlier step left the app on purpose (``home``, ``open_url``)."""
    ops = (i.get("action") for i in trace if isinstance(i, dict))
    return any(isinstance(a, dict) and a.get("op") in LEAVES_ON_PURPOSE for a in ops)


def _left_note(run: _ReplayRun, screen: object) -> str:
    """Why a failed assert may be about the wrong app, else an empty string."""
    package = _screen_package(screen)
    expected = run.ctx.package
    if not package or not expected or package == expected:
        return ""
    if package in tuple(run.ctx.expect_apps or ()) or _left_on_purpose(run.trace):
        return ""
    return (
        " (The foreground app is %s, not %s, so this text was looked for in "
        "the wrong app. Send `launch` to return before asserting again.)"
        % (package[:80], expected[:80])
    )


async def _assert_step(run: _ReplayRun, step: _Step, seen: Seen) -> dict | None:
    """An ``assert`` step: ``None`` when it passed and the replay goes on, else
    the reply that stops it (a failed assert, or a visual check)."""
    at = StepAt(step.entry["index"], step.entry, seen.screen)
    if str(getattr(step.action, "kind", "")) == "visual":
        return _visual_check(run, step, at)
    view = AssertView(seen.screen, run.trace, seen.baseline_texts)
    ok, detail = _evaluate_assert(step.action, view)
    if not ok:
        ok, detail, at = await recheck_after_wait(run, step, (view, at))
    if not ok:
        detail += _left_note(run, at.screen)
    step.entry.update(outcome="assert_pass" if ok else "assert_fail", detail=detail)
    _close(run, at)
    if ok:
        return None
    defer_shot(run, at.screen)
    return _reply(_result(_stop(run, at, STATUS_NEEDS_MODEL)))
