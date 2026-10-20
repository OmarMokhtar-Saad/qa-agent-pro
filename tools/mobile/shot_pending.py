"""Deferred stop pictures (B11) that the next device call must wait for.

The picture is only for the report, so the step reply does not wait for it. It
runs as a task and is registered here per device; ``adb_run.run_argv`` settles
the device's pending pictures before any other adb call, so a later action never
overlaps a late screencap (the PNG would show the wrong screen). The picture's
own task is exempt (``inside``). Event-driven: waits on the tasks, bounded.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar

#: Longest a device call waits for a deferred picture (s); then it goes on.
SETTLE_TIMEOUT_S = 10.0

_PENDING: dict[str, set] = {}
_INSIDE: ContextVar = ContextVar("_SHOT_INSIDE", default=False)


def inside() -> None:
    """Mark the current task as the picture task: its own adb calls never wait."""
    _INSIDE.set(True)


def track(serial: str, task: asyncio.Future) -> None:
    """Register *task* as a pending picture of *serial*."""
    tasks = _PENDING.setdefault(serial, set())
    tasks.add(task)
    task.add_done_callback(lambda t: _forget(serial, t))


def _forget(serial: str, task: asyncio.Future) -> None:
    tasks = _PENDING.get(serial)
    if tasks is None:
        return
    tasks.discard(task)
    if not tasks:
        _PENDING.pop(serial, None)  # no empty set stays behind


def serial_of(cmd: list[str]) -> str:
    """The serial after ``-s`` in an adb argv, or ``""``."""
    if "-s" in cmd and cmd.index("-s") + 1 < len(cmd):
        return str(cmd[cmd.index("-s") + 1])
    return ""


async def settle(serial: str) -> None:
    """Wait (bounded) for every deferred picture of *serial*; never raises."""
    pending = set(_PENDING.get(str(serial or ""), ()))
    if pending and not _INSIDE.get():
        await asyncio.wait(pending, timeout=SETTLE_TIMEOUT_S)


async def settle_all() -> None:
    """Settle every device (the report reads these PNGs)."""
    for serial in list(_PENDING):
        await settle(serial)
