"""Replay waits: the wait for the screen to change, and the watch after it."""

from __future__ import annotations

import time

from tools.mobile import a11y_events, watch
from tools.mobile.replay.context import Context
from tools.mobile.replay.context_consts import EXPLORE_CHANGED_WAIT_MAX_MS
from tools.mobile.replay.dump import _dump
from tools.mobile.replay.fresh_read import unchanged
from tools.mobile.replay.screen import _screen_hash, system_dialog_package
from tools.mobile.replay.waits_dialog import answer_new_dialog
from tools.mobile.replay.waits_gen import await_move
from tools.mobile.replay.waits_interrupt import _interruption_of
from tools.mobile.replay.waits_poll import _wait_deadline
from tools.mobile.replay.waits_prior import already_changed, is_spinning
from tools.mobile.replay.waits_spec import _secs
from tools.mobile.replay.waits_types import AfterWait


def _capped_ms(ctx: Context, ms: int) -> int:
    """*ms*, held to the explore lane's ceiling: a turn waits, then the model decides."""
    return (
        min(ms, EXPLORE_CHANGED_WAIT_MAX_MS)
        if getattr(ctx, "lane", "") == "explore"
        else ms
    )


# Notes on _wait_until_changed (kept here so the function stays short).
#
# A wait exists because the planner expects the screen to become something
# else; sleeping through the whole ``ms`` after it already has is the cheapest
# thing this lane was getting wrong.
#
# NO FIXED SLEEP. The dump is the poll (``waits_gen.await_move``): it re-dumps
# until the event clock's generation moves, or until the wait's own deadline,
# and each dump is cut at that deadline, so a slow dump cannot carry the wait
# past the ms it was given. Without an event clock the baseline is the content
# hash and a differing hash is the move.
#
# WHICH IDENTITY, and why it is the hash. ``_screen_id`` is package + activity
# + the top three texts and is deliberately coarse; a chat reply is appended at
# the BOTTOM, so the id is byte-identical. A moved generation whose tree hash
# equals the starting one is NOT a change (a blinking cursor, a clock): the
# wait re-baselines and keeps looking.
#
# The caller settles with ``redump=not reusable``. A change is reusable unless
# it is a spinner (a transient state); an unchanged wait is reusable when its
# last dump succeeded and the service is known fresh (``fresh_read.unchanged``).
async def _wait_until_changed(ctx: Context, screen: object, ms: int) -> tuple:
    """Wait until the screen is not the one we started on: ``(screen, changed, reusable)``."""
    before = _screen_hash(screen)
    if already_changed(ctx, before, screen):
        return screen, True, True
    deadline = _wait_deadline(ctx, _capped_ms(ctx, ms) / 1000.0)
    return await _changed_by_events(ctx, (screen, before), deadline)


async def _changed_by_events(ctx: Context, start: tuple, deadline: float) -> tuple:
    """Re-baselining move loop of :func:`_wait_until_changed`."""
    screen, before = start
    base = a11y_events.latest(ctx.serial) or before
    ticker, look = watch.Ticker("waiting for the screen to change"), None
    while time.monotonic() < deadline:
        await ticker.tick()
        moved, look = await await_move(ctx, base, deadline)
        if look is None or look.error:
            return screen, False, False
        screen = look.tree
        if not moved:
            break
        if _screen_hash(screen) != before:
            return screen, True, not is_spinning(screen)
        if system_dialog_package(screen):  # unchanged AND covered: stop waiting
            return screen, False, False
        base = look.clock or _screen_hash(screen)
    return await unchanged(ctx, (screen, look is not None), deadline)


def _changed_detail(op: str, wait_ms: int, changed: bool) -> str:
    """What a wait on the screen changing DID, not what it was allowed to do.

    Reporting "waited 8000ms" for a wait that came back in a quarter of a
    second is the true-shaped sentence this lane has been burned by before, and
    the trace is read back in the report and in the next packet.
    """
    if op == "wait":
        return (
            ("waited for the screen to change, inside the " + str(wait_ms) + "ms")
            if changed
            else ("waited " + str(wait_ms) + "ms")
        )
    return (
        "the screen changed"
        if changed
        else "the screen did not change in " + _secs(wait_ms)
    )


# WATCHED (B6): ``(screen, reusable, interruption)`` after a changed wait.
#
# A change that is a dialog, a call, another app or an error text is NAMED and
# the replay stops for the model, instead of an "ok" that hands the next
# action a screen the script never expected.
#
# A FOCUS change returns the pre-wait screen un-read (the probe saw a new
# window, no dump was taken), so on that path the screen is read once here:
# comparing the baseline with itself could never name the call or app that
# took focus. That read IS the post-wait screen, so the settle reuses it
# rather than dumping a second time.
#
# A dump that fails here is left to ``_settle``, which reports it as
# ``dump_failed`` unless the device names a disconnect.
async def _watch_after_wait(ctx: Context, screen: object, after: AfterWait) -> tuple:
    """WATCHED (B6): ``(screen, reusable, interruption)`` after a changed wait."""
    before_wait, changed, reusable = after
    interruption = ""
    if changed and screen is before_wait:
        reread = await _dump(ctx)
        if reread.get("error"):
            interruption = watch.classify_error(reread["error"])
        else:
            screen = reread.get("content")
            reusable = True
    if changed and not interruption:
        screen = await answer_new_dialog(ctx, screen, before_wait)
        interruption = _interruption_of(ctx, screen, before_wait)
    return screen, reusable, interruption
