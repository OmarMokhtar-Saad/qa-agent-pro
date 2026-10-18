"""Replay waits: idle by the screencap-hash probe, not by repeated dumps.

A dump costs 2-3 s, a probe reading about 0.4 s. The screen is idle once
``IDLE_FRAMES`` consecutive probe frames hash equal; then at most ONE dump is
taken, and none at all when the frame equals the "before" reading of the dump
already in hand (``screen_anchor.trusted_baseline``). A blank (FLAG_SECURE)
frame or a failed probe answers ``None``: the caller falls back to dumps.
"""

from __future__ import annotations

import time

from tools.mobile import screen_anchor, screen_probe
from tools.mobile.replay.context import Context, _sleep
from tools.mobile.replay.context_consts import (
    AUTO_SETTLE_MAX_S,
    DEFAULT_WAIT_UNTIL_TEXT_S,
    WAIT_POLL_S,
)
from tools.mobile.replay.probe_gate import PROBE_BUDGET_S
from tools.mobile.replay.screen import _screen_hash
from tools.mobile.replay.waits_idle_clock import clock_idle
from tools.mobile.replay.waits_poll import _read_now, _wait_deadline

IDLE_FRAMES = 2
TIMED_OUT = "timed_out"


async def _first(ctx: Context, screen: object) -> tuple:
    """``(frame, trusted)``: the opening reading; *trusted* when it brackets *screen*."""
    base = await screen_anchor.trusted_baseline(
        ctx.serial, _screen_hash(screen), PROBE_BUDGET_S
    )
    if base is not None:
        return base, True
    return await screen_probe.baseline(ctx.serial, PROBE_BUDGET_S), False


async def _next_frame(ctx: Context, deadline: float) -> object:
    """The next probe reading after a poll pause, ``TIMED_OUT`` at the deadline,
    ``None`` when unreadable or blank."""
    left = deadline - time.monotonic()
    if left <= 0:
        return TIMED_OUT
    await _sleep(min(WAIT_POLL_S, left))
    budget = min(PROBE_BUDGET_S, max(deadline - time.monotonic(), 0.5))
    now = await screen_probe.read(ctx.serial, budget)
    if now is None or await screen_probe.blank(ctx.serial, now.digest, budget):
        return None
    return now


async def _settle_frames(ctx: Context, first: object, deadline: float) -> object:
    """The reading that held ``IDLE_FRAMES`` in a row, ``TIMED_OUT`` or ``None``."""
    last, run = first, 1
    while run < IDLE_FRAMES:
        now = await _next_frame(ctx, deadline)
        if now is None or now == TIMED_OUT:
            return now
        run = run + 1 if screen_probe.same(last, now) else 1
        last = now
    return last


async def _timed_out_read(ctx: Context, screen: object) -> tuple:
    """A timed-out wait still answers with the screen as it is NOW: one dump,
    bounded by one scaled read, so the caller never stamps the old screen."""
    final, content = await _read_now(
        ctx, screen, _wait_deadline(ctx, AUTO_SETTLE_MAX_S)
    )
    return final or (False, content, True)


async def probe_idle(ctx: Context, screen: object, ms: int) -> tuple | None:
    """``(idle, screen, timed_out)`` from the event clock, else probe frames, or
    ``None`` to fall back to dumps."""
    clocked = await clock_idle(ctx, screen, ms)
    if clocked is not None:
        return clocked
    deadline = _wait_deadline(ctx, (ms / 1000.0) if ms else DEFAULT_WAIT_UNTIL_TEXT_S)
    first, trusted = await _first(ctx, screen)
    if first is None:
        return None
    settled = await _settle_frames(ctx, first, deadline)
    if settled == TIMED_OUT:
        return await _timed_out_read(ctx, screen)
    if settled is None:
        return None
    if trusted and screen_probe.same(first, settled):
        return True, screen, False
    final, content = await _read_now(ctx, screen, deadline)
    return final or (True, content, False)
