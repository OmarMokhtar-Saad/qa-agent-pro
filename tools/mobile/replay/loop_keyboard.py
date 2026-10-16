"""Replay engine: the QA keyboard around a script's typing. Cut verbatim from tools/mobile/replay/replay.py (C5 w17)."""

from __future__ import annotations

import re
from typing import Any

from tools.mobile import ime, imes_record
from tools.mobile.replay import keyboard_hold
from tools.mobile.replay.context import Context
from tools.mobile.replay.context_consts import STATUS_ERROR, STATUS_NEEDS_TESTER
from tools.mobile.replay.device_ops import keyboard_down, keyboard_up, needs_keyboard
from tools.mobile.replay.loop_steps import _replay_steps


# R1d: a keyboard that cannot come up no longer STOPS the run. The tester's
# keyboard is given back first (if the select had happened), the steps run
# with typing over stdin, and the result says so, why, and the exact fix.
async def _bring_up(ctx: Context, serial: str) -> tuple[Any, str]:
    """``(previous keyboard, fallback note)`` once the QA keyboard is tried."""
    run_id = str(getattr(ctx, "run_id", "") or "")
    carried = str(getattr(ctx, "typing_fallback", "") or "")
    # Decided ONCE per run: a later step does not retry a 16 s install.
    carried = carried or str(imes_record.read_fallback(run_id).get("error") or "")
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
    imes_record.write_fallback(run_id, detail)
    return None, ime.fallback_notice(detail, serial)


def _keyboard_note(fallback_note: str, down: dict) -> str:
    """The note the keyboard hand-back adds to the reply."""
    if fallback_note:
        return fallback_note
    if down.get("error"):
        return str(down["error"])
    return str((down.get("content") or {}).get("note") or "")


_KEYBOARD_WORDS = re.compile(r"\b(keyboard|typing|type|ime)\b")


def _keyboard_related(content: dict) -> bool:
    """True when why the script ended concerns typing or the keyboard."""
    why = str(content.get("reason") or "").lower()
    return bool(_KEYBOARD_WORDS.search(why))


def _with_note(replied: Any, note: str) -> Any:
    """*replied* with the keyboard *note* put on its content."""
    content = replied.get("content") if isinstance(replied, dict) else None
    if note and isinstance(content, dict):
        content["keyboard"] = note
        if _keyboard_related(content):
            content["reason"] = (str(content.get("reason") or "") + " " + note).strip()
    return replied


# A script with a ``type``/``clear`` step brings the QA keyboard up before its
# first step. A run with a run id then HOLDS it across its scripts
# (``keyboard_hold``); a script that stops for the tester, errors, raises or is
# cancelled (an abort) gives it back at once, so the tester never inherits it.
# Without a hold it goes down in a ``finally`` after every script. A keyboard
# that cannot come up never stops the run (``_bring_up``).
async def _replay_keyboard(script: Any, ctx: Context) -> dict:
    """Execute *script* with the QA keyboard up for its typing (why: above)."""
    if not needs_keyboard(script):
        return await _replay_steps(script, ctx)
    serial = str(getattr(ctx, "serial", "") or "")
    run_id = str(getattr(ctx, "run_id", "") or "")
    if keyboard_hold.is_held(serial, run_id):
        return await _replay_held(script, ctx)
    previous, fallback_note = await _bring_up(ctx, serial)
    if keyboard_hold.hold(serial, run_id, previous):
        return await _replay_held(script, ctx)
    return await _replay_and_restore(script, ctx, (previous, fallback_note))


#: Script ends after which the run stops typing: the hold is given back.
_ENDS_HOLD = frozenset({STATUS_NEEDS_TESTER, STATUS_ERROR})


def _ends_hold(replied: Any) -> bool:
    """True when the script errored or stopped for the tester."""
    if not isinstance(replied, dict) or replied.get("error"):
        return True
    content = replied.get("content")
    return isinstance(content, dict) and content.get("status") in _ENDS_HOLD


async def _replay_held(script: Any, ctx: Context) -> dict:
    """Run the steps on the run's held keyboard; a stop or a failure gives it back."""
    try:
        replied = await _replay_steps(script, ctx)
    except BaseException:
        await _give_back(ctx)
        raise
    if not _ends_hold(replied):
        return replied
    return _with_note(replied, _keyboard_note("", await _give_back(ctx)))


async def _give_back(ctx: Context) -> dict:
    """End the run's hold and put the remembered keyboard back."""
    run_id = str(getattr(ctx, "run_id", "") or "")
    previous = keyboard_hold.previous(run_id)
    keyboard_hold.forget(run_id)
    if previous is None:
        return {"error": None, "content": None}
    return await keyboard_down(str(getattr(ctx, "serial", "") or ""), previous)


async def _replay_and_restore(script: Any, ctx: Context, up: tuple) -> dict:
    """Run the steps, then give the tester's keyboard back (no hold)."""
    previous, fallback_note = up
    serial = str(getattr(ctx, "serial", "") or "")
    down: dict = {"error": None, "content": None}
    try:
        replied = await _replay_steps(script, ctx)
    finally:
        if previous is not None:
            down = await keyboard_down(serial, str(previous))
    return _with_note(replied, _keyboard_note(fallback_note, down))
