"""Replay: re-check a failed assert once the screen is still, after a change wait (H06).

Measured (``mrun-20261021-163646-1b4628`` TC-009): ``wait_until_changed`` passed
on a small change, the next ``assert text_absent`` failed "still on this screen",
and the target screen arrived a moment later. A failed assert that directly
follows a change wait waits (by probe frames, never a sleep) until the screen is
idle, reads once more and judges again; a second failure stands.
"""

from __future__ import annotations

from typing import Any

from tools.mobile.replay.context import _ReplayRun, _Step
from tools.mobile.replay.steps_stop import StepAt
from tools.mobile.replay.verdicts_assert import AssertView, _evaluate_assert
from tools.mobile.replay.waits_text import _wait_until_idle

#: How long the screen may take to go idle before the assert is judged again.
SETTLE_MS = 3000
_CHANGE_WAITS = ("wait_until_changed", "wait")


def follows_change_wait(trace: list, index: int) -> bool:
    """True when the step before *index* was a change wait that finished ``ok``."""
    for entry in reversed(trace):
        if entry.get("index") == index:
            continue
        action = entry.get("action") or {}
        return action.get("op") in _CHANGE_WAITS and entry.get("outcome") == "ok"
    return False


async def settled_screen(run: _ReplayRun, screen: Any) -> Any:
    """*screen* after the device went idle, or *screen* when it never did."""
    _found, latest, _timed_out = await _wait_until_idle(run.ctx, screen, SETTLE_MS)
    run.ctx.wait_interruption = ""
    return latest if isinstance(latest, dict) else screen


async def recheck_after_wait(
    run: _ReplayRun, step: _Step, seen: tuple[AssertView, StepAt]
) -> tuple[bool, str, StepAt]:
    """Judge a failed assert again on the settled screen when a change wait led in."""
    view, at = seen
    if not follows_change_wait(run.trace, step.entry["index"]):
        ok, detail = _evaluate_assert(step.action, view)
        return ok, detail, at
    late = await settled_screen(run, view.screen)
    ok, detail = _evaluate_assert(step.action, view._replace(screen=late))
    return ok, detail, at._replace(screen=late)
