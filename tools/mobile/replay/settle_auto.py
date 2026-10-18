"""Replay settle: wait for a moving screen with a cheap probe, then read once."""

from __future__ import annotations

import time
from typing import Any, NamedTuple

from tools.mobile import dump_latency, latency_scale, screen_anchor, step_timing
from tools.mobile.replay.context import Context, _sleep
from tools.mobile.replay.context_consts import AUTO_SETTLE_MAX_S, WAIT_POLL_S
from tools.mobile.replay.dump import _dump, _focus_probe
from tools.mobile.replay.probe_gate import wait_moved
from tools.mobile.replay.screen import _screen_hash
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
    return (await _auto_settle_seen(ctx, screen, before_hash)).screen


class Settled(NamedTuple):
    """The settle's read, and what it saw: did the frame move, did the read fail."""

    screen: Any
    frame_moved: bool
    read_failed: bool


# What the caller needs to tell "the tap did nothing" from "the picture moved
# and no tree could show it": a frame that moved while every tree stayed the
# same is NOT proof of no change (run 8f3169's stale a11y tree), so the caller
# reports it as unverified rather than ``no_change``.
async def _auto_settle_seen(ctx: Context, screen: Any, before_hash: str) -> Settled:
    """:func:`_auto_settle`, also saying whether the frame moved or the read failed."""
    deadline = _settle_deadline(ctx)
    until = deadline - (dump_latency.p90_ms(ctx.serial) or 0) / 1000.0
    moved = await _frame_moved(ctx, screen, (deadline, until))
    if moved is None:
        return Settled(screen, False, False)
    dumped = await _dump(ctx)
    if dumped.get("error"):
        return Settled(screen, moved, True)
    return Settled(dumped.get("content"), moved, False)


async def _frame_moved(ctx: Context, screen: Any, window: tuple) -> bool | None:
    """``None`` when the frame never moved (no read needed), else whether it moved."""
    deadline, until = window
    base = await screen_anchor.trusted_baseline(
        ctx.serial, _screen_hash(screen), deadline - time.monotonic()
    )
    if base is not None and time.monotonic() < until:
        # Frame probe equal before and after the read's own dump: a frame that
        # never moves leaves the first read standing, with NO second dump.
        if await wait_moved(ctx, base, until) is None:
            return None
        await _focus_stable(ctx, until)
        return True
    await _focus_wait(ctx, deadline, until)
    return False


async def _focus_wait(ctx: Context, deadline: float, until: float) -> None:
    """The focus-only wait used when no frame probe is available (error, FLAG_SECURE)."""
    before_focus = await _focus_probe(ctx.serial, deadline - time.monotonic())
    if await _focus_moves(ctx, before_focus, until):
        await _focus_stable(ctx, until)


async def _probe_skip(ctx: Context, screen: Any) -> bool:
    """True when the frame stayed equal to the trusted pre-action baseline of
    *screen* for the whole settle window: the post-action dump is then skipped.

    The window is the settle deadline PLUS one dump's p90: what the old settle
    and its dump together observed, so a skip never watches less than a dump
    would have. False (dump as before) on no trusted baseline, a moved frame,
    or any probe error.
    """
    base = screen_anchor.before_action(ctx.serial, _screen_hash(screen))
    if base is None:
        step_timing.note("skip_miss:" + screen_anchor.miss_reason(ctx.serial))
        return False
    until = _settle_deadline(ctx) + (dump_latency.p90_ms(ctx.serial) or 0) / 1000.0
    unmoved = await _unmoved(ctx, base, until)
    if unmoved:
        step_timing.mark("probe_skip")
    return unmoved


async def _unmoved(ctx: Context, base: Any, until: float) -> bool:
    """True when the frame never left *base* before *until*; a miss is ledgered."""
    try:
        unmoved = await wait_moved(ctx, base, until) is None
    except (OSError, ValueError, RuntimeError):
        step_timing.note("skip_miss:probe_error")
        return False
    if not unmoved:
        step_timing.note("skip_miss:screen_changed")
    return unmoved
