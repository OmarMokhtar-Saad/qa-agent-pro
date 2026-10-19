"""Replay settle: answer a known system dialog through a registered handler."""

from __future__ import annotations

import logging
import time
from typing import Any, NamedTuple

from tools.mobile import actions as actions_mod
from tools.mobile import adb, dialog_handlers
from tools.mobile.replay.context import Context
from tools.mobile.replay.context_consts import DIALOG_SETTLE_S, MAX_DIALOG_TAPS
from tools.mobile.replay.dump import _drop_carried, _dump, _focus_probe, dump_raw
from tools.mobile.replay.dump_held import take_held
from tools.mobile.replay.elements import (
    _center,
    actuated_element,
    element_label,
    resolve_tap_text,
)
from tools.mobile.replay.guard import (
    MAX_GUARD_NODE_CHARS,
    destructive_hit,
    guard_detail,
    screen_hit,
)
from tools.mobile.replay.loop_field_moved import _focus_left
from tools.mobile.replay.settle_cut import CutView, _cut_of, _expected_here
from tools.mobile.replay.settle_mark import SettleJob
from tools.mobile.replay.settle_perm import dialog_context, record_stop
from tools.mobile.replay.waits_dialog import set_answerer

logger = logging.getLogger("tools.mobile.executor")


class DialogTarget(NamedTuple):
    """The pruned-screen element a handler taps, and the screen it was found on."""

    element: dict
    screen: Any


class Tap(NamedTuple):
    """One handler action about to be performed: the action, where, on which screen."""

    action: Any
    point: Any
    screen: Any


# The pruned-screen element a handler's action names, or None when it is
# absent or ambiguous (no blind taps).
def _dialog_element(action: Any, screen: Any) -> dict | None:
    """The pruned-screen element a handler's action names, or None."""
    if action.kind == dialog_handlers.TAP_TEXT:
        found = resolve_tap_text(action.value, screen)
        chosen = None if found.get("ambiguous") else found.get("element")
    elif action.kind == dialog_handlers.TAP_RID:
        try:
            target = actions_mod.Target(rid=action.value)
        except ValueError:
            return None
        resolved = (actions_mod.resolve_target(target, screen) or {}).get("content")
        chosen = (resolved or {}).get("element")
    else:
        chosen = None
    return chosen if isinstance(chosen, dict) else None


# Why the destructive guard refuses a handler's tap on the element, or ''.
#
# The SAME derivation a tester's tap is judged by (the named label, the
# actuated node on its own strings, then screen_hit on the judged node), and
# deliberately NOT gated on ``ctx.guard_destructive``: a handler is not the
# tester. ``_consume_confirm`` is never called -- a confirm is bound to the
# tester's own op fingerprint, so a handler can never spend one.
def _dialog_tap_refusal(ctx: Context, action: Any, target: DialogTarget) -> str:
    """Why the destructive guard refuses a handler's tap on the element, or ''."""
    element, screen = target
    actuated = actuated_element(screen, _center(element))
    labels = [str(action.value)] if action.kind == dialog_handlers.TAP_TEXT else []
    labels.append(element_label(element, screen))
    if actuated is not None and actuated is not element:
        labels.append(element_label(actuated))
    judged = actuated if actuated is not None else element
    term = destructive_hit(" ".join(labels).strip())
    if not term:
        term = screen_hit(screen, "", judged=judged)
    return guard_detail(term, screen, ctx.package) if term else ""


async def _handler_action(ctx: Context, view: CutView, foreign: str) -> Any:
    """The handler's action for the interruption in front, or None when nothing fits."""
    # Settle's own dump was just read and nothing was actuated since (the
    # generation check inside take_held): do not read the same screen twice.
    raw = await take_held(ctx.serial) or await dump_raw(ctx.serial)
    xml = raw.get("content") if isinstance(raw, dict) else None
    if not isinstance(xml, str) or not xml:
        return None
    expected = frozenset({ctx.package, *_expected_here(ctx, view.extra)}) - {""}
    return dialog_handlers.handle(dialog_context(ctx, foreign, (expected, xml)))


def _dialog_gate(ctx: Context, action: Any, screen: Any) -> tuple:
    """``(point, refusal)``: where to tap, or why not (``point`` None = no usable control)."""
    element = _dialog_element(action, screen)
    point = _center(element) if element is not None else None
    if element is None or point is None:
        return None, ""
    return point, _dialog_tap_refusal(ctx, action, DialogTarget(element, screen))


async def _dialog_gone(ctx: Context, focus: str) -> None:
    """Wait, bounded by ``DIALOG_SETTLE_S``, until the focused window leaves *focus*.

    A condition wait, never a sleep: the dialog's own window is the one in
    focus before the tap, and the answer is a different window. An unknown
    focus (probe failed) or a dialog that shares its window's focus just runs
    out the bound.
    """
    if focus:
        await _focus_left(ctx, focus, time.monotonic() + DIALOG_SETTLE_S)


async def _tap_and_reread(ctx: Context, tap: Tap) -> dict | None:
    """Perform the tap, wait for the app to settle, re-read: the answered-dialog result."""
    back = tap.action.kind == dialog_handlers.BACK
    _drop_carried(ctx.serial)
    focus = await _focus_probe(ctx.serial, DIALOG_SETTLE_S)
    if back:
        tapped = await adb.keyevent(ctx.serial, "KEYCODE_BACK")
    else:
        tapped = await adb.tap(ctx.serial, tap.point[0], tap.point[1])
    if tapped.get("error"):
        return None
    label = "back" if back else str(tap.action.value)[:MAX_GUARD_NODE_CHARS]
    await _dialog_gone(ctx, focus)
    dumped = await _dump(ctx)
    if dumped.get("error"):
        return {"label": label, "screen": tap.screen, "stop": True}
    return {"label": label, "screen": dumped.get("content")}


# Answer ONE interruption: ``None`` when nothing was done (no usable raw
# dump, no handler recognised it, the control is absent, the tap failed),
# ``{'refused': text}`` when the guard stopped the tap, else ``{'label',
# 'screen'[, 'stop']}`` after the tap and a re-read.
async def _answer_one_dialog(ctx: Context, view: CutView, foreign: str) -> dict | None:
    """Answer ONE interruption: None, ``{'refused': text}``, or the tapped result."""
    action = await _handler_action(ctx, view, foreign)
    if action is None:
        return None
    if action.kind == dialog_handlers.STOP:
        return {"unclear": action.value}
    point, refusal = (None, "")
    if action.kind != dialog_handlers.BACK:
        point, refusal = _dialog_gate(ctx, action, view.screen)
        if point is None:
            return None
    if refusal:
        return {"refused": refusal[:400]}
    return await _tap_and_reread(ctx, Tap(action, point, view.screen))


# Answer up to :data:`MAX_DIALOG_TAPS` interruptions and return the screen
# as it stands. ONE ``dialog_handlers.handle`` per interruption. Stops when
# the interruption is gone, nothing recognises it, or the guard refuses the
# tap (recorded as ``entry['dialog_refused']``). With no handler registered it
# does nothing at all, so a run that never registered one is unchanged.
async def _answer_dialogs(job: SettleJob, view: CutView) -> Any:
    """Answer up to MAX_DIALOG_TAPS interruptions and return the screen as it stands."""
    if not dialog_handlers.handler_names():
        return view.screen
    answered: list = []
    for _ in range(MAX_DIALOG_TAPS):
        kind, foreign = _cut_of(job.ctx, view)
        step = await _answer_one_dialog(job.ctx, view, foreign) if kind else None
        if step is None:
            break
        if record_stop(job.entry, step):
            break
        answered.append(step["label"])
        view = view._replace(screen=step["screen"])
        if step.get("stop"):
            break
    if answered:
        job.entry["dialog_taps"] = answered
    return view.screen


set_answerer(_answer_dialogs)
