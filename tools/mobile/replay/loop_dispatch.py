"""Replay engine: the dispatch of a step that passed the guard (assert, wait or device op). Cut verbatim from tools/mobile/replay/replay.py (C5 w17)."""

from __future__ import annotations

from typing import Any

from tools.mobile import actions as actions_mod
from tools.mobile import screen_anchor
from tools.mobile.replay.context import _ReplayRun, _Step
from tools.mobile.replay.device_ops import _perform
from tools.mobile.replay.elements import _clamp_source
from tools.mobile.replay.elements_swipe import TouchSpec
from tools.mobile.replay.guard import _confirmed
from tools.mobile.replay.loop_field_empty import reconcile_empty_field
from tools.mobile.replay.loop_settle import (
    Acted,
    _device_error_stop,
    _next_observes,
    _settle_device,
)
from tools.mobile.replay.screen import _screen_hash
from tools.mobile.replay.skip_done import skip_if_done
from tools.mobile.replay.steps_assert import Seen, _assert_step
from tools.mobile.replay.verdicts import SCROLL_TARGET_MISSED
from tools.mobile.replay.waits import _changed_wait, _wait_spec
from tools.mobile.replay.waits import _condition_wait as condition_wait
from tools.mobile.replay.waits_prior import clear_action, note_action, note_calm
from tools.mobile.replay.waits_types import ChangedStep, CondStep

#: Waits that, once met, prove the screen settled for the optional taps after them.
CALM_PROOFS = frozenset({"wait_until_idle", "wait_until_gone"})


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
        got, reply = await condition_wait(
            run, CondStep(step.action, spec, step.entry), screen
        )
        if reply is None and step.op in CALM_PROOFS:
            note_calm(run.ctx, _screen_hash(got))
        return got, reply
    return await _changed_wait(run, ChangedStep(step.op, spec[2], step.entry), screen)


# ONE rectangle for the guard and the device -- see
# `_swipe_points`. Both read this packet's own root_bounds
# rather than each asking adb, whose answer expires.
def _fill_proof(outcome: dict) -> str:
    """``field 'X': N/N chars`` for a fill whose field was read back, else ``""``."""
    content = outcome.get("content") or {}
    if not content.get("chars"):
        return ""
    return "field %r: %s." % (content.get("field") or "?", content["chars"])


def _tapped_label(step: _Step) -> str:
    """The visible label of the control a tap really hit, else ``""``."""
    element = step.element if isinstance(step.element, dict) else {}
    for key in ("text", "label", "desc"):
        if element.get(key):
            return " ".join(str(element[key]).split())[:60]
    return ""


def _note_ok(step: _Step, outcome: dict) -> None:
    """Record the acted step as ok with its confirmed detail and notes."""
    entry = step.entry
    entry["outcome"] = "ok"
    entry["detail"] = _confirmed(
        entry, str((outcome.get("content") or {}).get("detail") or "")
    )
    if step.op in ("tap", "tap_text", "tap_text_if_present"):
        entry["tapped"] = _tapped_label(step)
    proof = _fill_proof(outcome)
    if proof:
        entry["detail"] = (proof + " " + entry["detail"]).strip()
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
    if not _next_observes(run, step, screen):
        await screen_anchor.ready(run.ctx.serial)
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
    if (skipped := await skip_if_done(run, step, seen.screen)) is not None:
        return skipped
    if step.op == "assert":
        return seen.screen, _assert_step(run, step, seen)
    if step.op in actions_mod.WAIT_OPS:
        return await _wait_step(run, step, seen.screen)
    return await _device_step(run, step, seen.screen)
