"""Replay engine: the dispatch of a step that passed the guard (assert, wait or device op). Cut verbatim from tools/mobile/replay/replay.py (C5 w17)."""

from __future__ import annotations

from typing import Any

from tools.mobile import actions as actions_mod
from tools.mobile.replay.context import _ReplayRun, _Step
from tools.mobile.replay.device_ops import _perform
from tools.mobile.replay.elements import _clamp_source
from tools.mobile.replay.elements_swipe import TouchSpec
from tools.mobile.replay.guard import _confirmed
from tools.mobile.replay.loop_field_empty import reconcile_empty_field
from tools.mobile.replay.loop_settle import Acted, _device_error_stop, _settle_device
from tools.mobile.replay.screen import _screen_hash
from tools.mobile.replay.steps_assert import Seen, _assert_step
from tools.mobile.replay.verdicts import SCROLL_TARGET_MISSED
from tools.mobile.replay.waits import _changed_wait, _condition_wait, _wait_spec
from tools.mobile.replay.waits_prior import clear_action, note_action
from tools.mobile.replay.waits_types import ChangedStep, CondStep


async def _wait_step(
    run: _ReplayRun, step: _Step, screen: Any
) -> tuple[Any, dict | None]:
    """Run a wait op: ``(screen, reply)``, ``reply`` is ``None`` to go on.

    Every wait is a condition with a bound (S17): the legacy `wait` maps onto
    the same four through `_wait_spec`.
    """
    spec = _wait_spec(step.action, run.items)
    if spec[0] != "changed":
        clear_action(run.ctx)
        return await _condition_wait(
            run, CondStep(step.action, spec, step.entry), screen
        )
    return await _changed_wait(run, ChangedStep(step.op, spec[2], step.entry), screen)


# ONE rectangle for the guard and the device -- see
# `_swipe_points`. Both read this packet's own root_bounds
# rather than each asking adb, whose answer expires.
def _note_ok(step: _Step, outcome: dict) -> None:
    """Record the acted step as ok with its confirmed detail and notes."""
    entry = step.entry
    entry["outcome"] = "ok"
    entry["detail"] = _confirmed(
        entry, str((outcome.get("content") or {}).get("detail") or "")
    )
    if entry.get("fill_note"):
        entry["detail"] = (entry["fill_note"] + " " + entry["detail"]).strip()
    if step.scroll_missed:
        entry["detail"] = (SCROLL_TARGET_MISSED + " " + entry["detail"]).strip()


async def _device_step(
    run: _ReplayRun, step: _Step, screen: Any
) -> tuple[Any, dict | None]:
    """Run a device op: ``(screen, reply)``, ``reply`` is ``None`` to go on."""
    entry = step.entry
    touch = TouchSpec(step.op, step.action, step.element, _clamp_source(screen))
    note_action(run.ctx, _screen_hash(screen))
    outcome = await _perform(touch, run.ctx)
    outcome = await reconcile_empty_field(run, Acted(outcome, entry, screen))
    read = outcome.get("read_screen")
    if read is not None:
        screen = read
    if outcome.get("error"):
        return screen, _device_error_stop(run, Acted(outcome, entry, screen))
    _note_ok(step, outcome)
    return await _settle_device(
        run, step, Acted(outcome, entry, screen, read is not None)
    )


async def _dispatch_op(
    run: _ReplayRun, step: _Step, seen: Seen
) -> tuple[Any, dict | None]:
    """Run an action that passed the guard: an assert, a wait or a device op.

    ``(screen, reply)``; ``reply`` is ``None`` when the replay goes on.
    """
    if step.op == "assert":
        return seen.screen, _assert_step(run, step, seen)
    if step.op in actions_mod.WAIT_OPS:
        return await _wait_step(run, step, seen.screen)
    return await _device_step(run, step, seen.screen)
