"""The server's own home-screen check, run before App Tester and launch.

A goal that opens with "confirm the home screen is visible" asks for a check
that must come BEFORE the app is installed and launched. Doing it on the host
turn closed the freshly launched app. This presses Home, waits until the
launcher holds the foreground (bounded), and returns what it saw so the
briefing can cite it.
"""

from __future__ import annotations

import asyncio
import time

from tools.mobile import adb

HOME_TIMEOUT_S = 6.0
HOME_POLL_S = 0.25
#: What ``press_home_and_observe`` returns when the key event failed.
PRESS_FAILED = "could not press Home on the device"
_HOME_INTENT = [
    "cmd package resolve-activity --brief "
    "-a android.intent.action.MAIN -c android.intent.category.HOME"
]


def _package(component: str) -> str:
    return str(component or "").strip().split("/", 1)[0]


async def launcher_package(serial: str) -> str:
    """The package of the device's default launcher, or ``""`` when unknown."""
    got = await adb.shell(serial, _HOME_INTENT)
    out = str(((got.get("content") or {}).get("out")) or "")
    lines = [ln for ln in out.splitlines() if "/" in ln]
    return _package(lines[-1]) if lines else ""


async def _foreground(serial: str) -> str:
    got = await adb.current_activity(serial)
    return _package(str(got.get("content") or ""))


async def _wait_for(serial: str, launcher: str) -> str:
    """Foreground package once it is the launcher, else the last one seen."""
    deadline = time.monotonic() + HOME_TIMEOUT_S
    seen = await _foreground(serial)
    while launcher and seen != launcher and time.monotonic() < deadline:
        await asyncio.sleep(HOME_POLL_S)
        seen = await _foreground(serial)
    return seen


async def press_home_and_observe(serial: str) -> str:
    """Press Home; one line saying whether the launcher is in front."""
    pressed = await adb.keyevent(serial, "KEYCODE_HOME")
    if pressed.get("error"):
        return PRESS_FAILED
    launcher = await launcher_package(serial)
    seen = await _wait_for(serial, launcher)
    if launcher and seen == launcher:
        return "Home pressed; the launcher `" + launcher + "` is in front"
    return (
        "Home pressed; the foreground is `"
        + (seen or "unknown")
        + "`, not the launcher"
    )
