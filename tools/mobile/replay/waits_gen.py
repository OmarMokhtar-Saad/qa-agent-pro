"""Replay settle: the two-phase event-clock wait after an action.

One question, answered by dumping and nothing else: did the screen change, and
has it stopped changing? The dump is the poll (the qa-ime fast dump is cheap and
stamps the device's event clock), so there is no sleep between polls.

* Phase 1 re-dumps until the screen MOVED past the baseline, for at most
  ``MOVE_S``. No move -> ``changed=False``.
* Phase 2 keeps dumping until the screen is quiet, for at most
  ``PROBE_BUDGET_S``. At the cap ``still_changing`` is set. The last tree read
  is always returned, so the caller needs no further dump.

Signals. MOVED is the event clock's ``generation`` (every event counts; the
event ``count`` on qa-ime 1.2.1, which has no generation). QUIET is
``clock.quiet_ms() >= QUIET_MS`` (window and content events only, measured on
the device). Without a clock (uiautomator fallback, old qa-ime) both use the
content hash: moved = the hash differs from the baseline hash, quiet = two
equal consecutive dumps. The baseline is an ``EventClock``, a hash string, or
``None`` (the first read becomes the baseline).
"""

from __future__ import annotations

import time
from typing import NamedTuple

from tools.mobile import a11y_events
from tools.mobile.replay.context import Context
from tools.mobile.replay.context_consts import MOVE_S, PROBE_BUDGET_S
from tools.mobile.replay.dump import DUMP_CUT_DETAIL, _dump
from tools.mobile.replay.screen import _screen_hash
from tools.mobile.replay.waits_gate import Gate, hold, token_of
from tools.mobile.replay.waits_quiet import QUIET_MS

NO_LIMIT = float("inf")


class Settled(NamedTuple):
    """What a settle saw: *changed*, the last *tree* read (``None`` when nothing was),
    whether it was *still_changing* at the cap, and whether a dump *failed*."""

    changed: bool
    tree: object
    still_changing: bool = False
    failed: bool = False


class Look(NamedTuple):
    """One dump: its tree, the event clock it stamped (or ``None``), its error."""

    tree: object
    clock: a11y_events.EventClock | None
    error: str = ""


def _now() -> float:
    """The monotonic clock; a seam so tests can run time by hand."""
    return time.monotonic()


def baseline_of(ctx: Context, screen: object) -> a11y_events.EventClock | str:
    """The change baseline for *screen*: the last event clock, else its hash."""
    return a11y_events.latest(ctx.serial) or _screen_hash(screen)


async def read_look(ctx: Context, until: float) -> Look:
    """One dump cut at *until*."""
    dumped = await _dump(ctx, deadline=until)
    clock = a11y_events.latest(ctx.serial)
    return Look(dumped.get("content"), clock, dumped.get("error") or "")


def _basis(look: Look) -> a11y_events.EventClock | str:
    """What a later dump is compared with: the clock, else the content hash."""
    return look.clock or _screen_hash(look.tree)


def _moved(base: a11y_events.EventClock | str, look: Look) -> bool:
    """True when *look* differs from *base* on the signal the baseline carries."""
    if isinstance(base, str):
        return _screen_hash(look.tree) != base
    if look.clock is None:
        return True  # the clock was lost: the screen cannot be shown unchanged
    if base.generation is not None and look.clock.generation is not None:
        return look.clock.generation != base.generation
    return look.clock.count != base.count


async def await_move(
    ctx: Context, base: a11y_events.EventClock | str | None, until: float
) -> tuple:
    """Phase 1: ``(moved, last_look)``; dump until the screen leaves *base* or *until*.

    ``last_look`` is ``None`` when no dump ran. A dump error ends the loop.
    """
    last, gate = None, Gate(token_of(ctx.serial), started=True)
    while _now() < until:
        await hold(ctx, gate, until)  # the tap's own read is not read twice
        last = await read_look(ctx, until)
        if last.error:
            return False, last
        if base is None:
            base = _basis(last)
        elif _moved(base, last):
            return True, last
    return False, last


def _is_quiet(look: Look, prev: Look | None) -> bool:
    """True when the device clock says quiet, or (no clock) two equal dumps."""
    if look.clock is not None:
        return look.clock.quiet_ms() >= QUIET_MS
    return prev is not None and _screen_hash(prev.tree) == _screen_hash(look.tree)


async def _until_quiet(ctx: Context, first: Look, until: float) -> tuple:
    """Phase 2: ``(last_look, still_changing)``."""
    prev, look = None, first
    while not _is_quiet(look, prev):
        if _now() >= until:
            return look, True
        prev, look = look, await read_look(ctx, until)
        if look.error:
            return look._replace(tree=prev.tree), look.error == DUMP_CUT_DETAIL
    return look, False


def _result(changed: bool, look: Look | None, still: bool) -> Settled:
    """The :class:`Settled` for the last *look*; a cut dump is not a failure."""
    if look is None:
        return Settled(changed, None)
    failed = bool(look.error) and look.error != DUMP_CUT_DETAIL
    return Settled(changed, look.tree, still, failed)


async def settle(
    ctx: Context, baseline: a11y_events.EventClock | str | None, until: float = NO_LIMIT
) -> Settled:
    """Wait for the screen to move past *baseline*, then to go quiet (see the module)."""
    start = _now()
    moved, look = await await_move(ctx, baseline, min(start + MOVE_S, until))
    if not moved or look is None:
        return _result(False, look, False)
    look, still = await _until_quiet(ctx, look, min(_now() + PROBE_BUDGET_S, until))
    return _result(True, look, still)


async def hold_still(ctx: Context, screen: object) -> Settled:
    """Phase 2 alone: *screen* once it stopped changing, from the screen in hand."""
    first = Look(screen, a11y_events.latest(ctx.serial))
    look, still = await _until_quiet(ctx, first, _now() + PROBE_BUDGET_S)
    return _result(True, look, still)
