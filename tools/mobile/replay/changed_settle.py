"""Replay waits: after a wait saw the screen change, hold still on the frame probe.

The wait ends at the FIRST change, which may be a screen still animating or
loading. Settling it with a full-window wait would add seconds to every stable
screen, so the check rides the cheap frame probe: one short quiet window on a
trusted baseline of the changed screen. Quiet keeps the screen in hand with no
extra dump; a move rebases and repeats until quiet or the settle cap, then ONE
dump reads what the frame settled on (a dump error keeps the screen in hand).
With no trusted baseline (FLAG_SECURE, an untaken anchor) the focus probe
stands in: no focus move keeps the screen, a move waits for focus to agree.
"""

from __future__ import annotations

import time

from tools.mobile import dump_latency, latency_scale, screen_anchor, screen_probe
from tools.mobile.replay.context import Context
from tools.mobile.replay.context_consts import AUTO_SETTLE_MAX_S, WAIT_POLL_S
from tools.mobile.replay.dump import _dump as read_dump
from tools.mobile.replay.dump import _focus_probe
from tools.mobile.replay.probe_gate import wait_moved
from tools.mobile.replay.screen import _screen_hash
from tools.mobile.replay.settle_auto import _focus_moves, _focus_stable

#: One quiet window: a sleep and a read, twice. A frame still for this long is settled.
QUIET_S = WAIT_POLL_S * 1.5


def _cut(ctx: Context) -> float:
    """The latest moment to stop watching: the settle cap minus one dump's p90."""
    p90 = dump_latency.p90_ms(ctx.serial)
    cap = latency_scale.scale_settle(AUTO_SETTLE_MAX_S, p90)
    return time.monotonic() + cap - (p90 or 0) / 1000.0


async def _frame_quiet(ctx: Context, base: screen_probe.Reading, cut: float) -> bool:
    """True when the frame holds still for one window; else rebase until quiet or *cut*.

    False once it moved at all (the screen in hand may be stale), or when the
    probe itself failed.
    """
    moved = False
    while time.monotonic() < cut:
        now = await wait_moved(ctx, base, min(cut, time.monotonic() + QUIET_S))
        if now is None:
            return not moved
        if not isinstance(now, screen_probe.Reading) or not now.digest:
            return False  # unreadable probe: say "moved", the caller dumps
        base, moved = now, True
    return not moved


async def _focus_quiet(ctx: Context, cut: float) -> bool:
    """The fallback without a frame baseline: True when the focus did not move."""
    before = await _focus_probe(ctx.serial, cut - time.monotonic())
    if not await _focus_moves(ctx, before, min(cut, time.monotonic() + QUIET_S)):
        return True
    await _focus_stable(ctx, cut)
    return False


async def _is_quiet(ctx: Context, screen: object) -> bool:
    """True when *screen* may stand: nothing moved while it was watched."""
    cut = _cut(ctx)
    base = await screen_anchor.trusted_baseline(
        ctx.serial, _screen_hash(screen), cut - time.monotonic()
    )
    if base is None:
        return await _focus_quiet(ctx, cut)
    return await _frame_quiet(ctx, base, cut)


async def settle_changed(ctx: Context, screen: object) -> object:
    """*screen* once it stopped moving: the one in hand when quiet, else one fresh dump."""
    if await _is_quiet(ctx, screen):
        return screen
    dumped = await read_dump(ctx)
    if dumped.get("error") or dumped.get("content") is None:
        return screen
    return dumped["content"]
