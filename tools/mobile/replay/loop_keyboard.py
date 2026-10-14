"""Replay engine: the QA keyboard around a script's typing. Cut verbatim from tools/mobile/replay/replay.py (C5 w17)."""

from __future__ import annotations

from typing import Any

from tools.mobile import ime
from tools.mobile.replay.context import Context
from tools.mobile.replay.device_ops import keyboard_down, keyboard_up, needs_keyboard
from tools.mobile.replay.loop_steps import _replay_steps


# R1d: a keyboard that cannot come up no longer STOPS the run. The tester's
# keyboard is given back first (if the select had happened), the steps run
# with typing over stdin, and the result says so, why, and the exact fix.
async def _bring_up(ctx: Context, serial: str) -> tuple[Any, str]:
    """``(previous keyboard, fallback note)`` once the QA keyboard is tried."""
    carried = str(getattr(ctx, "typing_fallback", "") or "")
    up: dict = (
        {"error": carried, "content": None} if carried else await keyboard_up(serial)
    )
    previous = (up.get("content") or {}).get("previous")
    if not up.get("error"):
        return previous, ""
    detail = str(up["error"])[:400]
    if previous is not None:
        await keyboard_down(serial, str(previous))
    ctx.typing_fallback = detail
    return None, ime.fallback_notice(detail, serial)


def _keyboard_note(fallback_note: str, down: dict) -> str:
    """The note the keyboard hand-back adds to the reply."""
    if fallback_note:
        return fallback_note
    if down.get("error"):
        return str(down["error"])
    return str((down.get("content") or {}).get("note") or "")


def _with_note(replied: Any, note: str) -> Any:
    """*replied* with the keyboard *note* put on its content."""
    content = replied.get("content") if isinstance(replied, dict) else None
    if note and isinstance(content, dict):
        content["keyboard"] = note
        content["reason"] = (str(content.get("reason") or "") + " " + note).strip()
    return replied


# A script with a ``type``/``clear`` step runs :func:`keyboard_up` before its
# first step and :func:`keyboard_down` in a ``finally`` -- success, failure,
# an exception and a cancelled task (an abort) all restore. A keyboard that
# cannot come up stops the run by name before any step, restoring first if
# the select had already happened. Otherwise the contract is
# :func:`_replay_steps`'s.
async def _replay_keyboard(script: Any, ctx: Context) -> dict:
    """Execute *script* with the QA keyboard up for its typing (why: above)."""
    if not needs_keyboard(script):
        return await _replay_steps(script, ctx)
    serial = str(getattr(ctx, "serial", "") or "")
    previous, fallback_note = await _bring_up(ctx, serial)
    down: dict = {"error": None, "content": None}
    try:
        replied = await _replay_steps(script, ctx)
    finally:
        if previous is not None:
            down = await keyboard_down(serial, str(previous))
    return _with_note(replied, _keyboard_note(fallback_note, down))
