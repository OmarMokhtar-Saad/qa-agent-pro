"""One ``current_ime`` read per step.

The keyboard-active check runs before every type/clear and again when the
keyboard is set up; each is an adb round trip. Within one armed step the answer
cannot change unless WE change it, so a clean read is kept against that step's
timing state (a fresh object per ``step_timing.begin()``) and dropped by
``invalidate`` whenever the keyboard is selected or restored. Outside an armed
step nothing is cached.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from tools.mobile import step_timing

_HELD: dict[str, tuple[object, dict]] = {}


async def current(serial: str, fetch: Callable[[str], Awaitable[dict]]) -> dict:
    """``fetch(serial)``, reused for the rest of the step when it read cleanly."""
    step = step_timing._PHASES.get()
    held = _HELD.get(serial)
    if step is not None and held is not None and held[0] is step:
        return held[1]
    result = await fetch(serial)
    if step is not None and not result.get("error"):
        _HELD[serial] = (step, result)
    return result


def invalidate(serial: str | None = None) -> None:
    """Forget the held read (all serials when *serial* is None)."""
    if serial is None:
        _HELD.clear()
    else:
        _HELD.pop(serial, None)
