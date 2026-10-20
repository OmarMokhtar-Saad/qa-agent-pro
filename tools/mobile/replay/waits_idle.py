"""Replay waits: ``wait_until_idle`` on the device's event clock, dumps otherwise."""

from __future__ import annotations

from tools.mobile.replay.context import Context
from tools.mobile.replay.waits_idle_clock import clock_idle


async def probe_idle(ctx: Context, screen: object, ms: int) -> tuple | None:
    """``(idle, screen, timed_out)`` from the event clock, or ``None`` to fall
    back to dump polling."""
    return await clock_idle(ctx, screen, ms)
