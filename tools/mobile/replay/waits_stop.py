"""Replay waits: the wait-for-change step and the stops it can end in."""

from __future__ import annotations

from tools.mobile import step_timing
from tools.mobile.replay.context import ReplayStop, _append, _ReplayRun, _result
from tools.mobile.replay.context_consts import STATUS_NEEDS_MODEL
from tools.mobile.replay.screen import _stamp_after
from tools.mobile.replay.settle_mark import SettleJob, SettleOpts
from tools.mobile.replay.settle_run import _settle
from tools.mobile.replay.waits_changed import (
    _changed_detail,
    _wait_until_changed,
    _watch_after_wait,
)
from tools.mobile.replay.waits_types import AfterWait, ChangedStep, StopView


def _interrupted_stop(run: _ReplayRun, view: StopView, interruption: str) -> dict:
    """The reply for a changed wait that a watched interruption cut short."""
    entry, screen = view
    entry["outcome"] = "interrupted"
    entry["detail"] = interruption
    _stamp_after(entry, screen)
    _append(run.trace, entry)
    stop = ReplayStop(
        STATUS_NEEDS_MODEL, run.trace, screen, "", interruption, entry["index"]
    )
    return {"error": None, "content": _result(stop)}


def _wait_timeout_stop(run: _ReplayRun, view: StopView) -> dict:
    """The reply for a non-`wait` op whose screen never changed.

    The model said the screen would change and it did not: hand it the screen
    rather than run the rest of a script written for a screen that never came.
    The legacy `wait` carries on, as it always has. The caller has already
    stamped the entry.
    """
    entry, screen = view
    entry["outcome"] = "wait_timeout"
    _append(run.trace, entry)
    stop = ReplayStop(
        STATUS_NEEDS_MODEL, run.trace, screen, "", entry["detail"], entry["index"]
    )
    return {"error": None, "content": _result(stop)}


async def _poll_changed(run: _ReplayRun, step: ChangedStep, screen: object) -> tuple:
    """Run the change wait and watch it: ``(screen, changed, reusable, interruption)``."""
    before_wait = screen
    screen, changed, reusable = await _wait_until_changed(run.ctx, screen, step.wait_ms)
    step.entry["outcome"] = "ok"
    step.entry["detail"] = _changed_detail(step.op, step.wait_ms, changed)
    after = AfterWait(before_wait, changed, reusable)
    screen, reusable, interruption = await _watch_after_wait(run.ctx, screen, after)
    return screen, changed, reusable, interruption


def _changed_finish(run: _ReplayRun, view: StopView, timed_out: bool) -> tuple:
    """Close the changed-wait entry: ``(screen, reply)``; a timed-out wait stops."""
    entry, screen = view
    _stamp_after(entry, screen)
    if timed_out:
        return screen, _wait_timeout_stop(run, view)
    _append(run.trace, entry)
    return screen, None


# `redump=not reusable`: only a wait whose LAST poll dumped the unchanged
# screen hands back a post-wait screen (`_wait_until_changed` says when).
# Every other path holds a screen from before the wait ended, and skipping
# the dump there would hand the next action a screen older than the wait
# itself -- the defect `_settle`'s docstring records.
async def _changed_wait(run: _ReplayRun, step: ChangedStep, screen: object) -> tuple:
    """A wait for the screen to change: ``(screen, reply)``.

    ``reply`` is ``None`` when the replay goes on to the next action.
    """
    entry = step.entry
    screen, changed, reusable, interruption = await _poll_changed(run, step, screen)
    if interruption:
        return screen, _interrupted_stop(run, StopView(entry, screen), interruption)
    if reusable:
        step_timing.mark("dump_reused")
    job = SettleJob(run.ctx, entry, run.trace, entry["index"])
    opts = SettleOpts(redump=not reusable, mark_no_change=False, op=step.op)
    screen, stop = await _settle(job, screen, opts)
    if stop is not None:
        return screen, {"error": None, "content": stop}
    timed_out = step.op != "wait" and not changed
    return _changed_finish(run, StopView(entry, screen), timed_out)
