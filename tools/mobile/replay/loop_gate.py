"""Replay engine: the optional per-step identity gate of a batched replay."""

from __future__ import annotations

from tools.mobile.replay.context import (
    STATUS_NEEDS_MODEL,
    _ReplayRun,
    _ReplayState,
    _result,
)
from tools.mobile.replay.loop_open import _fresh_entry
from tools.mobile.replay.steps_stop import StepAt, _close, _reply, _stop
from tools.mobile.replay.verdicts import _actuated

#: Trace outcome of a step the gate refused to start.
SCREEN_MISMATCH = "screen_mismatch"


def _mismatch_stop(run: _ReplayRun, at: StepAt, why: str) -> dict:
    """The reply for the step *at* whose live screen is not the one it was saved from."""
    at.entry.update(outcome=SCREEN_MISMATCH, detail=why)
    _close(run, at)
    stop = _stop(run, at, STATUS_NEEDS_MODEL)
    return _reply(_result(stop, selector_stale=True, actuated=_actuated(run.trace)))


async def gate_step(run: _ReplayRun, state: _ReplayState, index: int) -> dict | None:
    """The reply that ends the script before step *index*, else ``None``.

    Asks ``ctx.step_gate`` (when set) about the screen in hand; it may hand back a
    fresher screen it had to wait for, which becomes the loop's live screen.
    """
    gate = getattr(run.ctx, "step_gate", None)
    if gate is None:
        return None
    screen, why = await gate(index, state.screen)
    state.screen = screen
    if not why:
        return None
    entry = _fresh_entry(index, run.items[index], screen)
    return _mismatch_stop(run, StepAt(index, entry, screen), why)
