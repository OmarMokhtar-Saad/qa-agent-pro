"""Replay waits: the wait-for-change step and the stops it can end in."""

from __future__ import annotations

import time

from tools.mobile import step_timing
from tools.mobile.replay.context import ReplayStop, _append, _ReplayRun, _result
from tools.mobile.replay.context_consts import STATUS_NEEDS_MODEL
from tools.mobile.replay.dead_tap_note import note_dead_tap
from tools.mobile.replay.screen import _stamp_after
from tools.mobile.replay.settle_mark import SettleJob, SettleOpts
from tools.mobile.replay.settle_run import _settle
from tools.mobile.replay.waits_changed import (
    _capped_ms,
    _changed_detail,
    _wait_until_changed,
    _watch_after_wait,
)
from tools.mobile.replay.waits_gen import hold_still
from tools.mobile.replay.waits_interrupt import _interruption_of
from tools.mobile.replay.waits_late import CHANGED, SAME, late_detail, late_verdict
from tools.mobile.replay.waits_prior import origin_screen
from tools.mobile.replay.waits_progress import stall_detail, take_progress
from tools.mobile.replay.waits_spec import _secs, _unchanged_detail, stamp_early
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
    note_dead_tap(run.trace)
    stop = ReplayStop(
        STATUS_NEEDS_MODEL, run.trace, screen, "", entry["detail"], entry["index"]
    )
    return {"error": None, "content": _result(stop)}


def _stamp_unchanged(run: _ReplayRun, step: ChangedStep, seen: tuple) -> None:
    progress, ends, elapsed_ms = seen
    ran_ms = _capped_ms(run.ctx, step.wait_ms)
    step.entry["detail"] = _unchanged_detail(ran_ms, elapsed_ms)
    stamp_early(step.entry, elapsed_ms, ran_ms)
    stalled = stall_detail(progress, ends, _secs(ran_ms))
    step.entry["detail"] = stalled or step.entry["detail"]


async def _poll_changed(run: _ReplayRun, step: ChangedStep, screen: object) -> tuple:
    """Run the change wait and watch it: ``(screen, changed, reusable, interruption)``."""
    before_wait, origin = screen, origin_screen(run.ctx, screen)
    started = time.monotonic()
    screen, changed, reusable = await _wait_until_changed(run.ctx, screen, step.wait_ms)
    progress = take_progress(run.ctx)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    step.entry["outcome"] = "ok"
    step.entry["detail"] = _changed_detail(step.op, step.wait_ms, changed)
    if step.op != "wait" and not changed:
        _stamp_unchanged(run, step, (progress, (origin, screen), elapsed_ms))
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


def _after_read(run: _ReplayRun, step: ChangedStep, seen: tuple) -> bool:
    """True when the wait still timed out after the fresh read; else words the entry.

    *seen* is ``(screen before the wait, fresh screen after it)``. A change the
    wait missed is reported as a late change, not a timeout; an unreadable
    screen is "unknown", never "did not change".
    """
    verdict = late_verdict(*seen)
    if verdict == SAME:
        return True
    ran = _secs(_capped_ms(run.ctx, step.wait_ms))
    step.entry["detail"] = late_detail(verdict, ran)
    step.entry.pop("wait_early", None)
    return verdict != CHANGED


def _late_interruption(run: _ReplayRun, seen: tuple, timed_out: bool) -> str:
    """What took the screen when the wait changed only after its cap; else ``""``."""
    if timed_out:
        return ""
    return _interruption_of(run.ctx, seen[1], seen[0])


def _unchanged_verdict(run: _ReplayRun, step: ChangedStep, seen: tuple) -> tuple:
    """Judge a non-`wait` op whose wait saw no change: ``(screen, reply)``."""
    timed_out = _after_read(run, step, seen)
    late = _late_interruption(run, seen, timed_out)
    view = StopView(step.entry, seen[1])
    if late:
        return seen[1], _interrupted_stop(run, view, late)
    return _changed_finish(run, view, timed_out)


async def _settle_wait(run: _ReplayRun, step: ChangedStep, seen: tuple) -> tuple:
    """Settle the wait's read, *seen* being ``(screen, reusable)``: ``(screen, stop)``."""
    screen, reusable = seen
    job = SettleJob(run.ctx, step.entry, run.trace, step.entry["index"])
    opts = SettleOpts(redump=not reusable, mark_no_change=False, op=step.op)
    screen, stop = await _settle(job, screen, opts)
    return screen, (None if stop is None else {"error": None, "content": stop})


async def _hold_changed(run: _ReplayRun, entry: dict, screen: object) -> object:
    """The first change, held still; stamps ``still_changing`` on *entry* at the cap."""
    held = await hold_still(run.ctx, screen)
    if held.still_changing:
        entry["still_changing"] = True
    return screen if held.tree is None else held.tree


# `redump=not reusable`: only a wait whose LAST poll dumped the unchanged
# screen hands back a post-wait screen (`_wait_until_changed` says when).
# Every other path holds a screen from before the wait ended, and skipping
# the dump there would hand the next action a screen older than the wait
# itself -- the defect `_settle`'s docstring records.
async def _changed_wait(run: _ReplayRun, step: ChangedStep, screen: object) -> tuple:
    """A wait for the screen to change: ``(screen, reply)``.

    ``reply`` is ``None`` when the replay goes on to the next action.
    """
    entry, before_wait = step.entry, screen
    screen, changed, reusable, interruption = await _poll_changed(run, step, screen)
    if interruption:
        return screen, _interrupted_stop(run, StopView(entry, screen), interruption)
    if reusable:
        step_timing.mark("dump_reused")
    screen, stop = await _settle_wait(run, step, (screen, reusable))
    if stop is not None:
        return screen, stop
    if changed:
        screen = await _hold_changed(run, entry, screen)
    if step.op == "wait" or changed:
        return _changed_finish(run, StopView(entry, screen), False)
    return _unchanged_verdict(run, step, (before_wait, screen))
