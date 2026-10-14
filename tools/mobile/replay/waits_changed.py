"""Replay waits: the wait for the screen to change, and the watch after it."""

from __future__ import annotations

import time

from tools.mobile import watch
from tools.mobile.replay.context import Context, _sleep
from tools.mobile.replay.context_consts import WAIT_POLL_S
from tools.mobile.replay.dump import _dump, _focus_probe
from tools.mobile.replay.screen import _screen_hash
from tools.mobile.replay.waits_interrupt import _interruption_of
from tools.mobile.replay.waits_poll import _wait_deadline
from tools.mobile.replay.waits_spec import _secs
from tools.mobile.replay.waits_types import AfterWait


async def _focus_moved(serial: str, before_focus: str, deadline: float) -> bool:
    """True when the focused window is a known one different from *before_focus*."""
    focus = await _focus_probe(serial, deadline - time.monotonic())
    return bool(focus and focus != before_focus)


async def _changed_poll(ctx: Context, deadline: float) -> tuple:
    """Sleep one poll, then read: ``(kind, content)``, kind ``cut``, ``error`` or ``read``."""
    await _sleep(min(WAIT_POLL_S, max(0.0, deadline - time.monotonic())))
    if time.monotonic() >= deadline:
        # A dump started at the deadline has no budget and is cut before
        # it reads anything; the last read, at most one WAIT_POLL_S old,
        # stands for the post-wait screen instead.
        return "cut", None
    dumped = await _dump(ctx, deadline=deadline)
    if dumped.get("error"):
        # WHY THIS DOES NOT REPORT, where `_wait_until_text`'s same
        # failure does: this returns to a branch that immediately calls
        # `_settle(redump=True)`, whose OWN `_dump` runs against the same
        # device -- a device still failing is reported there as
        # `dump_failed`, and one that has recovered did not deserve a
        # failed step. Graded by
        # `test_a_dump_error_during_a_polled_wait_is_not_lost`.
        return "error", None
    return "read", dumped.get("content")


# Notes on _wait_until_changed (kept here so the function stays short).
#
# A wait exists because the planner expects the screen to become something
# else; sleeping through the whole ``ms`` after it already has is the cheapest
# thing this lane was getting wrong -- observed on run mrun-20260908-061316-
# a41b2a, where fixed sleeps sat between every action.
#
# NO FIXED SLEEP (S17, owner decision). There is no floor below which the wait
# is slept instead of polled, and nothing is slept after the last poll: an
# unchanged screen ends the wait at its bound, a changed one at the poll that
# sees it. Each poll's dump is cut at the deadline (``_dump(ctx,
# deadline=...)``), so even a dump slower than its p90 cannot carry the wait
# past the ms it was given.
#
# It does not decide the screen the model SEES on a change. The caller still
# settles with ``redump=True`` after an early return, so an intermediate screen
# -- a spinner replacing a button IS a change -- is re-read before it is handed
# over.
#
# WHICH IDENTITY, and why it is the hash. ``_screen_id`` is package + activity
# + the top three texts, and ``perception._screen_id`` says so: it is
# deliberately coarse because the report dedupes on it. A chat reply is
# appended at the BOTTOM, so the top three texts never move and the id is
# byte-identical -- which made this loop blind to the one event it exists to
# catch. ``_settle`` was moved to the hash for the same reason.
#
# ``reusable`` is True in ONE case only: the screen never changed and the wait
# ended on a poll whose dump SUCCEEDED, so that dump is the post-wait screen and
# the caller's settle skips its own (``redump=not reusable``). Every early
# return is ``reusable=False``. That screen can be up to one :data:`WAIT_POLL_S`
# old: a poll whose sleep reached the deadline dumps nothing, so the last
# successful read stands for the post-wait screen.
#
# FOCUS FIRST. Each iteration asks ``adb.current_activity`` -- bounded by the
# time left -- before paying for a dump. A focused window different from the one
# PROBED when the wait started (never ``ctx.activity``, which is memoised per
# replay) is a change, returned at once and never reusable. An empty answer on
# either side is unknown, never a change.
#
# The baseline is PROBED at the start, never read from `ctx.activity`: that is
# memoised once per replay (`resolve_activity`) and is stale after the first
# navigation, when every later probe would differ from it and end every wait at
# its first poll. An empty or failed probe is UNKNOWN: this wait skips the focus
# check and the dump path alone decides.
async def _wait_until_changed(ctx: Context, screen: object, ms: int) -> tuple:
    """Poll until the screen is not the one we started on: ``(screen, changed, reusable)``."""
    before = _screen_hash(screen)
    deadline = _wait_deadline(ctx, ms / 1000.0)
    before_focus = await _focus_probe(ctx.serial, deadline - time.monotonic())
    current = screen
    fresh = False
    ticker = watch.Ticker("waiting for the screen to change")
    while time.monotonic() < deadline:
        await ticker.tick()
        if before_focus and await _focus_moved(ctx.serial, before_focus, deadline):
            return current, True, False
        kind, content = await _changed_poll(ctx, deadline)
        if kind != "read":
            return current, False, fresh and kind == "cut"
        current, fresh = content, True
        if _screen_hash(current) != before:
            return current, True, False
    return current, False, fresh


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
        interruption = _interruption_of(ctx, screen, before_wait)
    return screen, reusable, interruption
