"""Replay waits: answer a system dialog that appears WHILE a wait is polling.

Settle answers the dialog a tap caused. A pop-up that arrives on its own (a
notification permission asked a moment after a screen shows) lands during a
wait, where it used to cut the script as an "interruption". The registered
dialog handlers (and the run's grant/deny choices) decide it here, exactly as
they do after a tap; a dialog nobody answers is left for the interruption rule.
"""

from __future__ import annotations

from typing import Any

from tools.mobile import dialog_handlers
from tools.mobile.replay.context import Context
from tools.mobile.replay.context_consts import MAX_DIALOG_TAPS
from tools.mobile.replay.screen import system_dialog_package
from tools.mobile.replay.settle_cut import CutView
from tools.mobile.replay.settle_mark import SettleJob

#: Set by ``settle_dialog`` when it loads (it imports the wait helpers, so this
#: module cannot import it back): the one answerer of a dialog on a screen.
_ANSWERER: list = []


def set_answerer(answerer: Any) -> None:
    _ANSWERER[:] = [answerer]


def _new_dialog(current: object, baseline: object) -> bool:
    """True when *current* is covered by a system dialog that *baseline* was not."""
    dialog = system_dialog_package(current)
    return bool(dialog) and dialog != system_dialog_package(baseline)


def _spent(state: dict) -> bool:
    """True once this wait has used its tap budget or got an unclear/refused answer."""
    taps = len(state.get("dialog_taps") or ())
    stopped = state.get("permission_unclear") or state.get("dialog_refused")
    return bool(stopped) or taps >= MAX_DIALOG_TAPS


def _keep(state: dict, entry: dict) -> None:
    """Fold what one answering left on *entry* into the wait's running *state*."""
    taps = list(state.get("dialog_taps") or ()) + list(entry.pop("dialog_taps", ()))
    state.update(entry)
    if taps:
        state["dialog_taps"] = taps


async def answer_unless_met(ctx: Context, probe: Any, seen: tuple) -> object:
    """:func:`answer_new_dialog` for a watched wait, unless *probe* is already met:
    a wait for the pop-up's own text must still find it."""
    previous, current, baseline = seen
    if probe.met(previous, current):
        return current
    return await answer_new_dialog(ctx, current, baseline)


async def answer_new_dialog(ctx: Context, current: object, baseline: object) -> object:
    """*current*, or the screen after a handler answered the dialog that just appeared.

    A dialog that stays up (or comes back) is answered at most MAX_DIALOG_TAPS
    times per wait, then left for the interruption rule to report."""
    if not _ANSWERER or not dialog_handlers.handler_names():
        return current
    if not _new_dialog(current, baseline) or _spent(ctx.wait_dialog):
        return current
    entry: dict = {}
    answered = await _ANSWERER[0](
        SettleJob(ctx, entry, [], 0), CutView(current, "wait")
    )
    _keep(ctx.wait_dialog, entry)
    return answered
