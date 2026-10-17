"""Running ONE dump per serial: time it, start it, find it, wait for it to end."""

from __future__ import annotations

import asyncio
import functools
import time
from typing import Any, Awaitable

from tools.mobile import step_timing
from tools.mobile.dump_latency_state import (
    _INFLIGHT,
    _record,
    before_dump,
    take_aside,
)


def _source() -> str:
    """The tree source the armed step noted for this dump, or ``""``."""
    row = step_timing.current_row(0)
    return row.source if row else ""


async def _timed(serial: str, awaitable: Awaitable[Any]) -> Any:
    before_dump(serial)  # e.g. screen_anchor's "before" probe
    step_timing.note_source("")  # so the source read below is THIS dump's
    take_aside(serial)
    started = time.monotonic()
    try:
        result = await awaitable
    except asyncio.CancelledError:
        raise
    except Exception:
        _record(serial, _net(serial, started), _source())
        raise
    # The source (a11y, uiautomator fallback) keeps a 0.1 s dump out of the
    # same p90 as a 2.5 s one.
    _record(serial, _net(serial, started), _source())
    return result


def _net(serial: str, started: float) -> float:
    """Seconds since *started* without the confirmation dumps inside it."""
    return max(0.0, time.monotonic() - started - take_aside(serial))


def _on_done(finished: asyncio.Future, key: str) -> None:
    if _INFLIGHT.get(key) is finished:
        _INFLIGHT.pop(key, None)
    if not finished.cancelled():
        # Retrieved, so a cut dump that later failed is not logged as an
        # exception nobody looked at. The next dump reports the device.
        finished.exception()


# The caller awaits it through ``asyncio.shield``, so a deadline cancels the
# caller's wait and never the dump. Call :func:`join` first: starting a
# second dump beside a running one is exactly what this module stops.
def start(serial: str, awaitable: Awaitable[Any]) -> asyncio.Task:
    """Run *awaitable* as THIS serial's one dump in flight; return its task."""
    key = str(serial or "")
    task = asyncio.ensure_future(_timed(key, awaitable))
    _INFLIGHT[key] = task
    task.add_done_callback(functools.partial(_on_done, key=key))
    return task


def inflight(serial: str) -> asyncio.Task | None:
    """This serial's dump still running on THIS event loop, or ``None``."""
    key = str(serial or "")
    task = _INFLIGHT.get(key)
    if task is None:
        return None
    try:
        same_loop = task.get_loop() is asyncio.get_running_loop()
    except RuntimeError:
        same_loop = False
    if task.done() or not same_loop:
        _INFLIGHT.pop(key, None)
        return None
    return task


def _left(end: float | None, loop: asyncio.AbstractEventLoop) -> float | None:
    """Seconds left until *end* on *loop*, or ``None`` when there is no deadline."""
    return None if end is None else end - loop.time()


# Wait until NO dump is in flight on this serial. True when none is left.
#
# Loops, because several callers can wait on the same dump: when it ends,
# the first one woken starts the next dump before the others run, and they
# must then wait for THAT one. A caller that gets True reaches :func:`start`
# with no ``await`` in between, so on one event loop two dumps never
# overlap. Never raises the dump's own error and never cancels it:
# ``asyncio.wait`` leaves the task alone when its caller is cancelled or
# times out.
# """
async def join(serial: str, timeout: float | None = None) -> bool:
    """True when no dump is left in flight on *serial* (see the comment above)."""
    loop = asyncio.get_running_loop()
    end = None if timeout is None else loop.time() + timeout
    while True:
        task = inflight(serial)
        if task is None:
            return True
        left = _left(end, loop)
        if left is not None and left <= 0:
            return False
        done, _ = await asyncio.wait({task}, timeout=left)
        if not done:
            return False
