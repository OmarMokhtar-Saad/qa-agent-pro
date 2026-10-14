"""Replay settle: keep reading until a moving screen has stopped."""

from __future__ import annotations

import time
from typing import Any

from tools.mobile import dump_latency, latency_scale
from tools.mobile.replay.context import Context, _sleep
from tools.mobile.replay.context_consts import AUTO_SETTLE_MAX_S, WAIT_POLL_S
from tools.mobile.replay.dump import _dump, _focus_probe
from tools.mobile.replay.screen import _screen_hash


def _settle_deadline(ctx: Context) -> float:
    """The wall-clock cut for one auto-settle, scaled to the device's dump latency."""
    return time.monotonic() + latency_scale.scale_settle(
        AUTO_SETTLE_MAX_S, dump_latency.p90_ms(ctx.serial)
    )


async def _next_read(ctx: Context, deadline: float) -> tuple[bool, Any]:
    """``(ok, content)``: one more poll's read; ``ok`` False at the deadline or a dump error."""
    left = deadline - time.monotonic()
    if left <= 0:
        return False, None
    await _sleep(min(WAIT_POLL_S, left))
    if time.monotonic() >= deadline:
        return False, None
    dumped = await _dump(ctx, deadline=deadline)
    if dumped.get("error"):
        return False, None
    return True, dumped.get("content")


# Owner decision (batch 3): poll every :data:`WAIT_POLL_S`. Once the content
# hash differs from *before_hash*, the screen is moving, and the read handed
# back is the first of two IDENTICAL reads in a row with the same focused
# activity -- a transition is not handed over mid-animation. A screen that
# never changes ends at :data:`AUTO_SETTLE_MAX_S` and the caller reports
# ``no_change`` exactly as before.
#
# Never raises and never fails the step: a dump error or a cut dump returns
# the latest good read, which is at worst the read the caller already had.
# Each dump is cut at the deadline, so the bound is the wall clock.
async def _auto_settle(ctx: Context, screen: Any, before_hash: str) -> Any:
    """Keep reading after an action whose first read showed no change."""
    deadline = _settle_deadline(ctx)
    current, moving, last = screen, False, None
    while True:
        ok, read = await _next_read(ctx, deadline)
        if not ok:
            return current
        current = read
        read_hash = _screen_hash(current)
        if not read_hash:
            return current
        moving = moving or read_hash != before_hash
        if not moving:
            continue
        focus = await _focus_probe(ctx.serial, deadline - time.monotonic())
        if (read_hash, focus) == last:
            return current
        last = (read_hash, focus)
