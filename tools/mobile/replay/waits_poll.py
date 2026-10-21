"""Replay waits: the watched condition poll behind the text, gone, idle and element waits."""

from __future__ import annotations

import time

from tools.mobile import dump_latency, latency_scale, watch
from tools.mobile.replay.context import Context
from tools.mobile.replay.context_consts import DEFAULT_WAIT_UNTIL_TEXT_S
from tools.mobile.replay.dump import DUMP_CUT_DETAIL, _dump
from tools.mobile.replay.screen import _screen_has_element, absent_from
from tools.mobile.replay.waits_dialog import answer_unless_met
from tools.mobile.replay.waits_early import quiet_exit
from tools.mobile.replay.waits_gate import Gate, hold
from tools.mobile.replay.waits_interrupt import _interruption_of
from tools.mobile.replay.waits_prior import is_spinning
from tools.mobile.replay.waits_quiet import settled
from tools.mobile.replay.waits_types import Probe


def _wait_deadline(ctx: Context, seconds: float) -> float:
    """The monotonic deadline for *seconds*, stretched by the device's p90 dump latency."""
    return time.monotonic() + latency_scale.scale_timeout(
        seconds, dump_latency.p90_ms(ctx.serial)
    )


async def read(ctx: Context, deadline: float) -> dict:
    """The dump one poll reads; the public seam tests replace."""
    return await _dump(ctx, deadline=deadline)


def interruption(ctx: Context, current: object, screen: object) -> object:
    """The device interruption *current* shows, or falsy; the public seam tests replace."""
    return _interruption_of(ctx, current, screen)


async def _read_now(ctx: Context, current: object, deadline: float) -> tuple:
    """One dump: ``(final_or_None, content)``.

    *final* is the ``(found, screen, timed_out)`` answer when the wait ends on
    this read (a cut dump or a device error); otherwise ``None``.
    """
    # Cut at the deadline like every other poll's dump, so a slow dump cannot
    # carry the wait past its bound; a cut is never a device interruption.
    dumped = await read(ctx, deadline=deadline)
    if dumped.get("error") == DUMP_CUT_DETAIL:
        return (False, current, True), None
    if dumped.get("error"):
        ctx.wait_interruption = watch.classify_error(dumped["error"])
        return (False, current, not ctx.wait_interruption), None
    return None, dumped.get("content")


async def _next_read(ctx: Context, current: object, deadline: float) -> tuple:
    """Read: ``(final_or_None, content)``. The dump is the poll, so there is no
    pause before it; at the deadline the wait is over."""
    if time.monotonic() >= deadline:
        return (False, current, True), None
    return await _read_now(ctx, current, deadline)


async def advance_ticker(ticker: watch.Ticker) -> None:
    """One progress tick between two polls of a wait."""
    await ticker.tick()


def _ended(ctx: Context, probe: Probe, seen: tuple):
    """``(met, current, False)`` once the wait is met or interrupted, else ``None``."""
    previous, current, screen = seen
    if probe.met(previous, current):
        return True, current, False
    return (False, current, False) if _interrupted(ctx, current, screen) else None


def _interrupted(ctx: Context, current: object, screen: object) -> bool:
    """Name a device interruption on *ctx*; True when the wait must stop."""
    found = interruption(ctx, current, screen)
    if found:
        ctx.wait_interruption = str(found)
    return bool(found)


async def _begin(ctx: Context, screen: object, probe: Probe) -> tuple:
    """``(deadline, ticker, screen)`` a watched poll starts from."""
    budget = (probe.ms / 1000.0) if probe.ms else DEFAULT_WAIT_UNTIL_TEXT_S
    deadline = _wait_deadline(ctx, budget)
    screen = await settled(ctx, screen, deadline)
    return deadline, watch.Ticker(probe.label), screen


# ``probe.met(previous, current)`` is the condition; ``previous`` is ``None``
# until a poll has replaced the screen the wait started on.
async def _wait_for(ctx: Context, screen: object, probe: Probe) -> tuple:
    """THE watched condition poll behind the text, gone and idle waits."""
    if probe.met(None, screen):  # already true: no probe, no dump
        return True, screen, False
    return await _watch(ctx, screen, probe)


async def _watch(ctx: Context, screen: object, probe: Probe) -> tuple:
    """The poll loop of :func:`_wait_for` for a condition not yet met."""
    deadline, ticker, current = await _begin(ctx, screen, probe)
    previous, quiet, gate = None, 0, Gate(met=probe.met if probe.single else None)
    while True:
        current = await answer_unless_met(ctx, probe, (previous, current, screen))
        if ended := _ended(ctx, probe, (previous, current, screen)):
            return ended
        if (held := await hold(ctx, gate, deadline)) is not None:
            previous, current = current, held  # a hold read met the wait
            continue
        final, content = await _next_read(ctx, current, deadline)
        if final:
            return final
        quiet, previous, current = (quiet + 1) * (content == current), current, content
        if early_exit(ctx, probe, (quiet, current)):
            return False, current, True
        await advance_ticker(ticker)


def early_exit(ctx: Context, probe: Probe, seen: tuple) -> bool:
    """True when a wait for text may give up: a quiet, non-loading screen."""
    quiet, current = seen
    return probe.early and not is_spinning(current) and quiet_exit(ctx.serial, quiet)


def _element_met(rid: str, text: str, gone: bool):
    """The ``met`` of an element wait; a blank read never proves it gone (B7)."""

    def met(_previous: object, current: object) -> bool:
        has = _screen_has_element(current, rid, text)
        return absent_from(current, has) if gone else has

    return met


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
        Probe(ms, _element_met(rid, text, gone), label),
    )
