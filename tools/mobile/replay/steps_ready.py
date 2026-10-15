"""Replay engine: the learned wait before a step, as a condition wait.

The learned timing is only an UPPER BOUND: the step's target is waited for
(poll until it is on the screen) and the wait returns at once when the screen in
hand already shows it. A step with no target to wait for waits not at all. A wait
that saw its target measured the app, so it stays in the step's ms; a wait that
ran to its bound without it is our own, so its ms are stamped on the entry
(``waited_ms``) and the timings stage never learns it back. Every wait shows on
the step ledger as ``learned_wait``.
"""

from __future__ import annotations

import time
from typing import Any

from tools.mobile import actions as actions_mod
from tools.mobile import knowledge_hooks, knowledge_plug_timings, step_timing
from tools.mobile.replay.screen_has import _screen_has_element
from tools.mobile.replay.waits_poll import _wait_until_element
from tools.mobile.replay.waits_types import ElementWait


def _bound_ms(run: Any, action: Any, ahead: Any) -> int:
    """The wait's upper bound in ms: learned, capped by the max wait and deadline."""
    ms = knowledge_hooks.step_wait(run.ctx, action, ahead.screen)
    left = int((ahead.deadline - time.monotonic()) * 1000)
    return max(0, min(ms, int(actions_mod.MAX_WAIT_MS), left))


async def _poll_target(run: Any, rid: str, ahead: Any) -> Any:
    """Poll for *rid* within the bound: ``(met, the screen the poll ended on)``."""
    want = ElementWait(rid, "", False, _bound_ms(run, ahead.step[1], ahead))
    met, screen, _late = await step_timing.timed(
        "learned_wait", _wait_until_element(run.ctx, ahead.screen, want)
    )
    run.ctx.wait_interruption = ""
    return bool(met), screen if isinstance(screen, dict) else ahead.screen


async def _adaptive_wait(run: Any, action: Any, ahead: Any) -> Any:
    """Wait (bounded) for the step's target; the screen to run the step on."""
    rid = knowledge_plug_timings.step_op_rid(action)[1]
    if not rid or _bound_ms(run, action, ahead) <= 0:
        return ahead.screen
    if _screen_has_element(ahead.screen, rid):
        return ahead.screen
    began = time.monotonic()
    met, screen = await _poll_target(run, rid, ahead)
    if not met:
        ahead.step[2]["waited_ms"] = int((time.monotonic() - began) * 1000)
    return screen
