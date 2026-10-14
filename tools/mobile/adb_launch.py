"""Starting and stopping an app: ``launch`` and its helpers, ``force_stop`` (moved from adb.py)."""

from __future__ import annotations

import asyncio

from tools.device_ids import valid_package_name
from tools.mobile.adb_consts_dev import LAUNCH_START_TIMEOUT_S
from tools.mobile.adb_host import host


# What ``-W`` was used for, without its failure mode: ``-W`` waits for the
# app to report a completed DRAW, which an overloaded host can take the whole
# timeout to deliver. This asks the cheap question (``dumpsys window``) and
# stops the moment the answer is yes. Each read's own timeout is cut to the
# time left, so the poll cannot outlive its bound by a hung read. Never
# raises and never fails the launch.
async def wait_foreground(serial: str, package: str) -> None:
    """Poll the focused window until *package* holds it, for a bounded time."""
    deadline = host().time.monotonic() + host().LAUNCH_FOREGROUND_TIMEOUT_S
    while True:
        remaining = deadline - host().time.monotonic()
        if remaining <= 0:
            return
        focus = await host().current_activity(serial, timeout=int(remaining) + 1)
        if str(focus.get("content") or "").split("/", 1)[0] == package:
            return
        await asyncio.sleep(
            min(
                host().LAUNCH_FOREGROUND_POLL_S,
                max(0.0, deadline - host().time.monotonic()),
            )
        )


# BY COMPONENT FIRST, monkey second. ``am force-stop`` flags a package
# STOPPED, and a stopped package is excluded from implicit intent resolution
# -- which is all ``monkey -c LAUNCHER`` sends. Measured on the API 35 Play
# image: after the lane's own force-stop, monkey injected no event and the
# app never came up, so every case was refused as "did not come to the
# foreground". An explicit ``am start -n <component>`` does not go through
# implicit resolution and starts the app. Monkey remains the path when no
# component resolves (a device that lacks ``cmd package``), so nothing that
# launched before stops launching.
async def launch(serial: str, package: str) -> dict:
    """Start *package*'s launcher activity."""
    if not valid_package_name(package):
        return {
            "error": "Refusing to launch " + repr(str(package)[:60]),
            "content": None,
        }
    component = await host().resolve_launcher(serial, package)
    if component:
        return await host()._launch_component(serial, str(package), component)
    return await host()._launch_monkey(serial, str(package))


async def _launch_component(serial: str, package: str, component: str) -> dict:
    """``am start -n <component>``, then wait (bounded) for the app's window."""
    started = await host().shell(
        serial, ["am", "start", "-n", component], timeout=LAUNCH_START_TIMEOUT_S
    )
    body = started.get("content") or {}
    text = str(body.get("out") or "") + str(body.get("err") or "")
    if started.get("error") or "Error" in text:
        return {
            "error": (
                "Could not start "
                + component
                + ": "
                + (str(started.get("error") or text).strip()[:300] or "no reply")
            ),
            "content": None,
        }
    await host().wait_foreground(serial, package)
    return started


async def _launch_monkey(serial: str, package: str) -> dict:
    """The fallback launch for a device with no resolvable launcher component."""
    monkeyed = await host().shell(
        serial,
        [
            "monkey",
            "-p",
            str(package),
            "-c",
            "android.intent.category.LAUNCHER",
            "1",
        ],
        timeout=LAUNCH_START_TIMEOUT_S,
    )
    if not monkeyed.get("error"):
        await host().wait_foreground(serial, str(package))
    return monkeyed


async def force_stop(serial: str, package: str) -> dict:
    if not valid_package_name(package):
        return {"error": "Refusing to stop " + repr(str(package)[:60]), "content": None}
    return await host().shell(serial, ["am", "force-stop", str(package)])
