"""Preflight evidence: device lock state, foreground package, a dialog in front.

A system dialog in front (permission controller, installer) is answered by the
generic ``dialog_handlers`` -- a visible, non-denylisted control only, no
app-specific label -- and a bounded condition poll waits for the foreground to
change. A dialog nothing recognised is reported, never tapped blindly.
"""

from __future__ import annotations

import asyncio
import re
import time

from tools.mobile import adb, dialog_defaults
from tools.mobile import preflight_rows as rows
from tools.mobile.adb_device_ui import AdbDeviceUi
from tools.mobile.dialog_handlers import (
    BACK,
    TAP_RID,
    DialogAction,
    DialogContext,
    handle,
)

MAX_DISMISS = 3
CHANGE_TIMEOUT_S = 3.0
POLL_S = 0.2
_LOCK = re.compile(
    r"(?:isKeyguardShowing|mKeyguardShowing|mShowingLockscreen)\s*=\s*(true|false)"
)


def parse_lock(dump: str) -> str:
    """``locked`` / ``unlocked`` / ``unknown`` from a ``dumpsys window`` text."""
    found = _LOCK.findall(dump or "")
    if not found:
        return "unknown"
    return "locked" if "true" in found else "unlocked"


async def lock_state(serial: str) -> str:
    got = await adb.shell(serial, ["dumpsys window | grep -i keyguard"])
    return parse_lock(str(((got.get("content") or {}).get("out")) or ""))


async def foreground(serial: str) -> str:
    got = await adb.current_activity(serial)
    return str(got.get("content") or "").split("/", 1)[0]


async def _apply(ui: AdbDeviceUi, action: DialogAction) -> None:
    if action.kind == BACK:
        await ui.press_back()
    elif action.kind == TAP_RID:
        await ui.tap_resource_id(action.value)
    else:
        await ui.tap_text(action.value)


async def _wait_changed(serial: str, before: str) -> str:
    deadline = time.monotonic() + CHANGE_TIMEOUT_S
    now = await foreground(serial)
    while now == before and time.monotonic() < deadline:
        await asyncio.sleep(POLL_S)
        now = await foreground(serial)
    return now


async def _dismiss_once(serial: str, front: str, target: str) -> str:
    """One generic answer to the dialog in front; the new foreground package."""
    ui = AdbDeviceUi(serial)
    shot = await ui.screen()
    ctx = DialogContext(serial, front, frozenset({target}), shot.xml if shot else "")
    action = handle(ctx)
    if action is None:
        return front
    await _apply(ui, action)
    return await _wait_changed(serial, front)


async def clear_dialog(serial: str, target: str) -> tuple[str, int]:
    """``(foreground, answers)`` after answering recognised dialogs in front."""
    dialog_defaults.register_default_handlers()
    front, answered = await foreground(serial), 0
    while front and front != target and answered < MAX_DISMISS:
        after = await _dismiss_once(serial, front, target)
        if after == front:
            break
        front, answered = after, answered + 1
    return front, answered


async def screen_row(serial: str, target: str) -> dict:
    """The ``device_screen`` row: lock state, foreground, dialogs answered."""
    lock = await lock_state(serial)
    front, answered = await clear_dialog(serial, target)
    detail = f"lock={lock}; foreground={front or 'unknown'}"
    if answered:
        detail += f"; answered {answered} system dialog(s)"
    # A foreground other than the target with no dialog answered stays ok: before
    # launch the home screen or launcher is the normal foreground.
    stuck = bool(front) and front != target and answered >= MAX_DISMISS
    fix = "A system dialog is still in front; answer it on the device." if stuck else ""
    return rows.record_row("device_screen", rows._Row(not stuck, detail, fix))
