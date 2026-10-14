"""Replay screen carry: the dump one call hands to the next and the focus probe that guards it."""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import time
from typing import Any

from tools.mobile import (
    adb,
    step_timing,
)
from tools.mobile.replay.context_ctx import Context

#: How old a carried screen may be and still stand for the next call's first
#: read. The tester can touch the phone between calls, so this is short.
REUSE_MAX_AGE_S = 15.0


#: Devices whose last screen is carried; the least recently read is dropped.
MAX_CARRIED_SERIALS = 16


#: Seconds the live focus probe may take when confirming a carried screen.
REUSE_PROBE_S = 3.0


@dataclasses.dataclass(frozen=True)
class _Carried:
    """The last observed screen of one device and what it was read against."""

    result: dict
    taken: float
    package: str
    activity: str


_CARRIED: dict = {}


def _drop_carried(serial: str) -> None:
    """Forget the carried screen: something that can change it is about to run."""
    _CARRIED.pop(str(serial or ""), None)


def _remember(ctx: Context, observed: Any) -> None:
    """Keep a successful observation for the next call when the context allows."""
    if not ctx.carry_screen:
        return
    content = observed.get("content") if isinstance(observed, dict) else None
    if not isinstance(content, dict) or observed.get("error"):
        _drop_carried(ctx.serial)
        return
    key = str(ctx.serial or "")
    _CARRIED.pop(key, None)
    _CARRIED[key] = _Carried(
        copy.deepcopy(observed),
        time.monotonic(),
        str(content.get("package") or ""),
        str(content.get("activity") or ""),
    )
    while len(_CARRIED) > MAX_CARRIED_SERIALS:
        del _CARRIED[next(iter(_CARRIED))]


async def _take_carried(ctx: Context) -> dict | None:
    """The carried screen when it is still safe to treat as a fresh read.

    Used at most once (it is popped), and only when it is young, belongs to the
    run's package, and the device's LIVE focus still equals the activity it was
    read on. An unknown focus is not a match. Any actuating op, and the keyboard
    swaps, drop it before this is reached.
    """
    if not ctx.carry_screen:
        return None
    held = _CARRIED.pop(str(ctx.serial or ""), None)
    if held is None or time.monotonic() - held.taken > REUSE_MAX_AGE_S:
        return None
    if ctx.package and held.package != ctx.package:
        return None
    focus = await _focus_probe(ctx.serial, REUSE_PROBE_S)
    if not focus or focus != held.activity:
        return None
    step_timing.mark("dump_carried")
    return held.result


async def _focus_probe(serial: str, budget: float) -> str:
    """The focused ``package/Activity``, or ``""`` when not known in *budget* s.

    One short ``dumpsys window`` line, far cheaper than a dump. Bounded by the
    budget the polled wait has left, and never raises: an unknown focus is
    never a change.
    """
    if budget <= 0:
        return ""
    try:
        probed = await asyncio.wait_for(
            adb.current_activity(serial, timeout=int(budget) + 1), budget
        )
    except (TimeoutError, OSError, ValueError, KeyError):
        return ""
    return str((probed or {}).get("content") or "")
