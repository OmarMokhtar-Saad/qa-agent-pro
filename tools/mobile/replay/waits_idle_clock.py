"""Replay waits: ``wait_until_idle`` on the device's a11y event clock.

With a clock (qa-ime 1.2.1+) the screen is idle once the last window/content
event is ``QUIET_MS`` old, or once two consecutive dumps hash equal (a screen
that never goes quiet, e.g. a blinking cursor). Each read is a fast dump, so a
calm screen answers in about one dump. No clock, or a dump error: ``None``, and
the screencap probe path runs instead.
"""

from __future__ import annotations

import time

from tools.mobile import a11y_events
from tools.mobile.replay.context import Context
from tools.mobile.replay.context import _sleep as sleep
from tools.mobile.replay.context_consts import DEFAULT_WAIT_UNTIL_TEXT_S
from tools.mobile.replay.dump import _dump as dump
from tools.mobile.replay.screen import _screen_hash
from tools.mobile.replay.waits_poll import _wait_deadline
from tools.mobile.replay.waits_quiet import QUIET_MS, _rest_s


def idle_now(clock: a11y_events.EventClock, same: bool) -> bool:
    """True when the event clock is quiet or the last two trees are equal."""
    return same or clock.quiet_ms() >= QUIET_MS


async def clock_idle(ctx: Context, screen: object, ms: int) -> tuple | None:
    """``(idle, screen, timed_out)`` from the event clock, or ``None`` to fall back."""
    if a11y_events.latest(ctx.serial) is None:
        return None
    deadline = _wait_deadline(ctx, (ms / 1000.0) if ms else DEFAULT_WAIT_UNTIL_TEXT_S)
    last, seen = screen, None
    while time.monotonic() < deadline:
        dumped = await dump(ctx, deadline=deadline)
        clock = a11y_events.latest(ctx.serial)
        if dumped.get("error") or clock is None:
            return None
        last = dumped.get("content") or last
        digest = _screen_hash(last)
        if idle_now(clock, digest == seen):
            return True, last, False
        seen = digest
        await sleep(min(_rest_s(clock), max(0.0, deadline - time.monotonic())))
    return False, last, True
