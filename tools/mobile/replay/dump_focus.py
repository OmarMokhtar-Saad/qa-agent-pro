"""Replay focus probe, shared: ONE ``dumpsys window`` per step per distinct screen.

The fallback path used to probe at the dump (`dump_screen`), at the carry guard
(`dump_carry`) and at the held-dump guard (`dump_held`) for the same screen.
An answer is reused only inside the armed step that read it, under the same
actuation generation (`dump_reuse.token`) and for the same dump content, so a
new step, an actuation or a changed screen always asks again. No time-based
TTL. An unarmed caller (no step) is never cached; an unknown ("") is not kept.
"""

from __future__ import annotations

import hashlib
from typing import Any

from tools.mobile import adb, dump_reuse, step_timing

_SCREEN: dict[str, str] = {}
_ANSWER: dict[str, tuple] = {}


def _bounded(table: dict) -> None:
    while len(table) > dump_reuse.MAX_HELD_SERIALS:
        del table[next(iter(table))]


def note_screen(serial: str, xml: Any) -> None:
    """Record the content digest of the dump now being observed on *serial*."""
    key = str(serial or "")
    digest = hashlib.sha1(str(xml or "").encode("utf-8", "replace")).hexdigest()
    _SCREEN.pop(key, None)
    _SCREEN[key] = digest
    _bounded(_SCREEN)


def _stamp(serial: str) -> tuple | None:
    step = step_timing.armed_step()
    if step is None:
        return None
    return (step, dump_reuse.token(serial), _SCREEN.get(serial, ""))


async def current(serial: str, timeout: int | None = None) -> str:
    """The focused ``package/Activity``; one adb call per step/generation/screen."""
    key = str(serial or "")
    stamp = _stamp(key)
    held = _ANSWER.get(key)
    if stamp is not None and held is not None and held[0] == stamp:
        return held[1]
    kwargs = {} if timeout is None else {"timeout": timeout}
    probed = await adb.current_activity(key, **kwargs)
    value = str((probed or {}).get("content") or "")
    if stamp is not None and value:
        _ANSWER.pop(key, None)
        _ANSWER[key] = (stamp, value)
        _bounded(_ANSWER)
    return value


def reset() -> None:
    """Forget every held answer and screen digest."""
    _SCREEN.clear()
    _ANSWER.clear()
