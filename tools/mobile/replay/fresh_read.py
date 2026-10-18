"""Replay waits: one forced fresh read before a wait says "the screen did not change".

A "no change" that rests on the same hash as the baseline can be a stale tree
(the framework node cache, a dump held from earlier). The read here drops every
held dump of the device and dumps once more; only a screen that still equals the
baseline is reported unchanged. qa-ime 1.2.2+ clears its own node cache before
every dump and reports a generation, so a dump it just served is fresh already.
"""

from __future__ import annotations

import time

from tools.mobile import a11y_events, dump_cut_keep, dump_reuse
from tools.mobile.replay.context import Context
from tools.mobile.replay.context_consts import FRESH_READ_BUDGET_S
from tools.mobile.replay.dump import _dump
from tools.mobile.replay.screen import _screen_hash


def dump_known_fresh(serial: str) -> bool:
    """True when the device's last dump came from a service that clears its cache."""
    clock = a11y_events.latest(str(serial or ""))
    return clock is not None and clock.generation is not None


async def fresh_read(ctx: Context, screen: object, deadline: float) -> tuple:
    """``(screen, changed, reusable)`` after one dump with every held dump dropped.

    A failed read keeps *screen*, not reusable: the caller's settle reads again.
    """
    serial = str(ctx.serial or "")
    dump_cut_keep.take(serial)
    dump_reuse.bump(serial)
    until = max(deadline, time.monotonic() + FRESH_READ_BUDGET_S)
    dumped = await _dump(ctx, deadline=until)
    if dumped.get("error"):
        return screen, False, False
    content = dumped.get("content")
    return content, _screen_hash(content) != _screen_hash(screen), True


async def unchanged(ctx: Context, read: tuple, deadline: float) -> tuple:
    """The end of a dump-polled wait with no change: *read* is ``(screen, fresh)``.

    A screen that was dumped in the wait is confirmed by one fresh read unless the
    service is known to clear its cache; one never dumped is left to the settle.
    """
    screen, fresh = read
    if not fresh:
        return screen, False, False
    if dump_known_fresh(ctx.serial):
        return screen, False, True
    return await fresh_read(ctx, screen, deadline)
