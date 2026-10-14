"""Replay settle: wait for a moving screen with a cheap probe, then read once."""

from __future__ import annotations

import time
from typing import Any

from tools.mobile import dump_latency, latency_scale
from tools.mobile.replay.context import Context, _sleep
from tools.mobile.replay.context_consts import AUTO_SETTLE_MAX_S, WAIT_POLL_S
from tools.mobile.replay.dump import _dump, _focus_probe
from tools.mobile.replay.waits_changed import _focus_moved


def _settle_deadline(ctx: Context) -> float:
    """The wall-clock cut for one auto-settle, scaled to the device's dump latency."""
    return time.monotonic() + latency_scale.scale_settle(
        AUTO_SETTLE_MAX_S, dump_latency.p90_ms(ctx.serial)
    )


async def _focus_moves(ctx: Context, before_focus: str, until: float) -> bool:
    """Poll the focus probe (one short ``dumpsys`` line, not a dump) until *until*.

    True as soon as the focused window differs from *before_focus*. An unknown
    baseline never counts as a move.
    """
    while before_focus and time.monotonic() < until:
        await _sleep(min(WAIT_POLL_S, max(0.0, until - time.monotonic())))
        if await _focus_moved(ctx.serial, before_focus, until):
            return True
    return False


async def _focus_stable(ctx: Context, until: float) -> None:
    """Poll the cheap probe until two reads in a row agree, or *until* passes."""
    last = await _focus_probe(ctx.serial, until - time.monotonic())
    while time.monotonic() < until:
        await _sleep(min(WAIT_POLL_S, max(0.0, until - time.monotonic())))
        now = await _focus_probe(ctx.serial, until - time.monotonic())
        if now == last:
            return
        last = now


# S2: the old loop paid a FULL dump every WAIT_POLL_S until two reads matched,
# which on a ~2.4s dump meant 2-3 dumps per action whose screen had not moved
# yet. Now a cheap focus probe waits for the window to move and ONE full dump
# reads the result. The probe runs until the deadline minus one dump's p90, so
# a screen that never moves still ends at the deadline with that single dump
# (an in-place change, the case this settle exists for, is read by it). A move
# gets the read only once two probes in a row agree on the focus
# (``_focus_stable``), so a screen still mid-transition is not what is read.
#
# Never raises and never fails the step: a dump error returns the screen the
# caller already had, and the caller reports ``no_change`` as before.
# ``before_hash`` stays in the signature for the caller; the caller compares.
async def _auto_settle(ctx: Context, screen: Any, before_hash: str) -> Any:
    """After an action whose first read showed no change: probe cheaply, read once."""
    deadline = _settle_deadline(ctx)
    before_focus = await _focus_probe(ctx.serial, deadline - time.monotonic())
    until = deadline - dump_latency.p90_ms(ctx.serial) / 1000.0
    if await _focus_moves(ctx, before_focus, until):
        await _focus_stable(ctx, until)
    dumped = await _dump(ctx)
    return screen if dumped.get("error") else dumped.get("content")
