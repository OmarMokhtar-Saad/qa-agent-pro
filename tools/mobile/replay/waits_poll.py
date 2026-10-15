"""Replay waits: the watched condition poll behind the text, gone, idle and element waits."""

from __future__ import annotations

import time

from tools.mobile import dump_latency, latency_scale, watch
from tools.mobile.replay.context import Context, _sleep
from tools.mobile.replay.context_consts import (
    DEFAULT_WAIT_UNTIL_TEXT_S,
    WAIT_POLL_S,
)
from tools.mobile.replay.dump import DUMP_CUT_DETAIL, _dump
from tools.mobile.replay.screen import _screen_has_element
from tools.mobile.replay.waits_interrupt import _interruption_of
from tools.mobile.replay.waits_types import Pace, Probe


def _wait_deadline(ctx: Context, seconds: float) -> float:
    """The monotonic deadline for *seconds*, stretched by the device's p90 dump latency."""
    return time.monotonic() + latency_scale.scale_timeout(
        seconds, dump_latency.p90_ms(ctx.serial)
    )


async def _read_now(ctx: Context, current: object, deadline: float) -> tuple:
    """One dump: ``(final_or_None, content)``.

    *final* is the ``(found, screen, timed_out)`` answer when the wait ends on
    this read (a cut dump or a device error); otherwise ``None``.
    """
    # Cut at the deadline like every other poll's dump, so a slow dump cannot
    # carry the wait past its bound; a cut is never a device interruption.
    dumped = await _dump(ctx, deadline=deadline)
    if dumped.get("error") == DUMP_CUT_DETAIL:
        return (False, current, True), None
    if dumped.get("error"):
        ctx.wait_interruption = watch.classify_error(dumped["error"])
        return (False, current, not ctx.wait_interruption), None
    return None, dumped.get("content")


async def _next_read(ctx: Context, current: object, pace: Pace) -> tuple:
    """Pause, then read: ``(final_or_None, content, read_s)``.

    ``read_s`` is how long the dump took, so the next pause can shrink by it.
    """
    deadline = pace.deadline
    if time.monotonic() >= deadline:
        return (False, current, True), None, 0.0
    await _sleep(min(pace.pause, max(0.0, deadline - time.monotonic())))
    if time.monotonic() >= deadline:
        return (False, current, True), None, 0.0
    started = time.monotonic()
    final, content = await _read_now(ctx, current, deadline)
    return final, content, time.monotonic() - started


def _pause_after(took: float) -> float:
    """The pause before the next read: a dump that already took a poll interval
    or longer is itself the pause, so none is added."""
    return 0.0 if took >= WAIT_POLL_S else WAIT_POLL_S


# ``probe.met(previous, current)`` is the condition; ``previous`` is ``None``
# until a poll has replaced the screen the wait started on.
async def _wait_for(ctx: Context, screen: object, probe: Probe) -> tuple:
    """THE watched condition poll behind the text, gone and idle waits."""
    budget = (probe.ms / 1000.0) if probe.ms else DEFAULT_WAIT_UNTIL_TEXT_S
    deadline = _wait_deadline(ctx, budget)
    previous, current, took = None, screen, 0.0
    ticker = watch.Ticker(probe.label)
    while True:
        if probe.met(previous, current):
            return True, current, False
        interruption = _interruption_of(ctx, current, screen)
        if interruption:
            ctx.wait_interruption = interruption
            return False, current, False
        final, content, took = await _next_read(
            ctx, current, Pace(deadline, _pause_after(took))
        )
        if final:
            return final
        previous, current = current, content
        await ticker.tick()


async def _wait_until_element(ctx: Context, screen: object, want: tuple) -> tuple:
    """Poll until an element with resource id ``rid`` is on the screen (or, with
    ``gone``, is not). ``want`` is an ``ElementWait``; the answer is
    ``(satisfied, latest_screen, timed_out)``; ``ms == 0`` falls back to the
    default budget. Watched and hard-bounded by :func:`_wait_for`.
    """
    rid, text, gone, ms = want
    label = "waiting for element " + repr(str(rid)[:40]) + (" to go" if gone else "")
    return await _wait_for(
        ctx,
        screen,
        Probe(
            ms,
            lambda _previous, current: _screen_has_element(current, rid, text) != gone,
            label,
        ),
    )
