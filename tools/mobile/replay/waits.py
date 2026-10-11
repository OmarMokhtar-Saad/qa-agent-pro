"""Replay engine: wait-until steps and condition polling. Cut verbatim from tools/mobile/executor.py (P4.5)."""

from __future__ import annotations

import logging
import time

from tools.mobile import actions as actions_mod
from tools.mobile import (
    dump_latency,
    latency_scale,
    step_timing,
    watch,
)
from tools.mobile.replay.context import (
    DEFAULT_WAIT_UNTIL_TEXT_S,
    LEGACY_WAIT_MAX_MS,
    STATUS_NEEDS_MODEL,
    WAIT_POLL_S,
    Context,
    _append,
    _ReplayRun,
    _result,
    _sleep,
)
from tools.mobile.replay.dump import DUMP_CUT_DETAIL, _dump, _focus_probe
from tools.mobile.replay.screen import (
    _screen_has,
    _screen_has_element,
    _screen_hash,
    _screen_package,
    _stamp_after,
    _texts,
    left_app_detail,
    system_dialog_detail,
    system_dialog_package,
)
from tools.mobile.replay.settle import _settle

logger = logging.getLogger("tools.mobile.executor")


async def _wait_until_text(
    ctx: Context, screen: object, text: str, ms: int, *, gone: bool = False
) -> tuple[bool, object, bool]:
    """Poll the screen until *text* appears (or, with *gone*, is no longer on
    it) or the budget runs out.

    Returns ``(found, latest_screen, timed_out)``. ``ms`` is the caller's cap;
    ``0`` falls back to :data:`DEFAULT_WAIT_UNTIL_TEXT_S` so a script that
    forgot to set it does not poll forever.

    WATCHED (B6/S16): a poll that finds something NEW in front of the app -- a
    system dialog, a call, another app, an error text -- or a device that
    stopped answering ends the wait at once with ``(False, screen, False)``
    and the sentence in ``ctx.wait_interruption``. It used to keep polling a
    covered screen until the whole budget was gone.
    """
    if gone:
        return await _wait_for(
            ctx,
            screen,
            ms,
            lambda _previous, current: not _screen_has(current, text),
            "waiting for " + repr(str(text)[:40]) + " to go",
        )
    return await _wait_for(
        ctx,
        screen,
        ms,
        lambda _previous, current: _screen_has(current, text),
        "waiting for " + repr(str(text)[:40]),
    )


async def _wait_until_idle(
    ctx: Context, screen: object, ms: int
) -> tuple[bool, object, bool]:
    """Poll until two reads in a row are the same screen (S17).

    The screen in hand is NOT the first of the two: it may be a read taken
    mid-animation, so idle is only ever claimed from two polls. Same return
    and interruptions as :func:`_wait_until_text`.
    """
    return await _wait_for(
        ctx,
        screen,
        ms,
        lambda previous, current: (
            previous is not None and _screen_hash(previous) == _screen_hash(current)
        ),
        "waiting for the screen to settle",
    )


async def _wait_for(
    ctx: Context,
    screen: object,
    ms: int,
    met,
    label: str,
) -> tuple[bool, object, bool]:
    """THE watched condition poll behind the text, gone and idle waits.

    ``met(previous, current)`` is the condition; ``previous`` is ``None``
    until a poll has replaced the screen the wait started on.
    """
    budget = (ms / 1000.0) if ms else DEFAULT_WAIT_UNTIL_TEXT_S
    deadline = time.monotonic() + latency_scale.scale_timeout(
        budget, dump_latency.p90_ms(ctx.serial)
    )
    previous: object = None
    current = screen
    ticker = watch.Ticker(label)
    while True:
        if met(previous, current):
            return True, current, False
        interruption = _interruption_of(ctx, current, screen)
        if interruption:
            ctx.wait_interruption = interruption
            return False, current, False
        if time.monotonic() >= deadline:
            return False, current, True
        await _sleep(min(WAIT_POLL_S, max(0.0, deadline - time.monotonic())))
        if time.monotonic() >= deadline:
            return False, current, True
        # Cut at the deadline like every other poll's dump, so a slow dump
        # cannot carry the wait past its bound. A cut is the bound running
        # out, never a device interruption.
        dumped = await _dump(ctx, deadline=deadline)
        if dumped.get("error") == DUMP_CUT_DETAIL:
            return False, current, True
        if dumped.get("error"):
            ctx.wait_interruption = watch.classify_error(dumped["error"])
            return False, current, not ctx.wait_interruption
        previous, current = current, dumped.get("content")
        await ticker.tick()


def _wait_spec(action: object, items: object) -> tuple[str, str, int]:
    """``(kind, text, ms)`` for any wait op: the one mapping from the legacy
    ``wait`` onto the condition waits (S17, owner decision).

    ``wait until_text`` is ``wait_until_text``, with its split bare share of
    the total when it set no ms; ``wait ms`` is ``wait_until_changed`` bounded
    by that ms and clipped to :data:`LEGACY_WAIT_MAX_MS` -- still parsed, never
    refused, and never a fixed sleep.
    """
    op = str(getattr(action, "op", "") or "")
    if op == "wait":
        until_text = str(getattr(action, "until_text", "") or "").strip()
        ms = int(getattr(action, "ms", 0) or 0)
        until_rid = str(getattr(action, "until_rid", "") or "").strip()
        if until_rid:
            return "element", until_rid, ms or actions_mod.bare_wait_ms_each(items)
        if until_text:
            return "text", until_text, ms or actions_mod.bare_wait_ms_each(items)
        return "changed", "", min(ms, LEGACY_WAIT_MAX_MS)
    ms = int(float(getattr(action, "max_s", 0) or 0) * 1000)
    kind = op[len("wait_until_") :]
    return kind, str(getattr(action, "text", "") or "").strip(), ms


def _wait_words(kind: str, text: str) -> tuple[str, str, str]:
    """``(met, timed_out, doing)`` -- the trace sentences for one wait kind."""
    quoted = repr(text[:120])
    if kind == "gone":
        return (
            quoted + " is gone",
            "timed out waiting for " + quoted + " to go",
            "waiting for " + quoted + " to go",
        )
    if kind == "idle":
        return (
            "the screen settled",
            "timed out waiting for the screen to settle",
            "waiting for the screen to settle",
        )
    return (
        "found " + quoted,
        "timed out waiting for " + quoted,
        "waiting for " + quoted,
    )


def _secs(ms: int) -> str:
    """``8000`` -> ``"8s"``, ``2500`` -> ``"2.5s"``."""
    return ("%g" % (ms / 1000.0)) + "s"


def _interruption_of(ctx: Context, screen: object, baseline: object) -> str:
    """``"interrupted: <what>"`` for what is NEW on *screen* since *baseline*.

    ``""`` when nothing new took the screen. Only NEW facts count: a dialog
    that was already up when the wait began is the screen the model chose to
    wait on, not an interruption of it.
    """
    package = _screen_package(screen)
    before_package = _screen_package(baseline)
    named = watch.classify_package(package, before_package)
    if named:
        return named
    dialog = system_dialog_package(screen)
    if dialog and dialog != system_dialog_package(baseline):
        return watch.PREFIX + system_dialog_detail(dialog)
    if package and ctx.package and package != ctx.package and before_package != package:
        return watch.PREFIX + left_app_detail(package, ctx.package)
    return watch.classify_texts(_texts(screen) - _texts(baseline))


async def _wait_until_element(
    ctx: Context,
    screen: object,
    rid: str,
    text: str,
    gone: bool,
    ms: int,
) -> tuple[bool, object, bool]:
    """Poll until an element with resource id *rid* is on the screen (or, with
    *gone*, is not). ``(satisfied, latest_screen, timed_out)``; ``ms == 0``
    falls back to the default budget. Watched and hard-bounded by
    :func:`_wait_for`, the same as the text, gone and idle waits.
    """
    return await _wait_for(
        ctx,
        screen,
        ms,
        lambda _previous, current: _screen_has_element(current, rid, text) != gone,
        "waiting for element " + repr(str(rid)[:40]) + (" to go" if gone else ""),
    )


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
async def _wait_until_changed(
    ctx: Context, screen: object, ms: int
) -> tuple[object, bool, bool]:
    """Poll until the screen is no longer the one we started on, or *ms* runs out.

    Returns ``(latest_screen, changed, reusable)``; see the notes above.
    """
    before = _screen_hash(screen)
    deadline = time.monotonic() + latency_scale.scale_timeout(
        ms / 1000.0, dump_latency.p90_ms(ctx.serial)
    )
    # The baseline is PROBED here, never read from `ctx.activity`: that is
    # memoised once per replay (`resolve_activity`) and is stale after the
    # first navigation, when every later probe would differ from it and end
    # every wait at its first poll. An empty or failed probe is UNKNOWN: this
    # wait skips the focus check and the dump path alone decides.
    before_focus = await _focus_probe(ctx.serial, deadline - time.monotonic())
    current = screen
    fresh = False
    ticker = watch.Ticker("waiting for the screen to change")
    while time.monotonic() < deadline:
        await ticker.tick()
        if before_focus:
            focus = await _focus_probe(ctx.serial, deadline - time.monotonic())
            if focus and focus != before_focus:
                return current, True, False
        await _sleep(min(WAIT_POLL_S, max(0.0, deadline - time.monotonic())))
        if time.monotonic() >= deadline:
            # A dump started at the deadline has no budget and is cut before
            # it reads anything; the last read, at most one WAIT_POLL_S old,
            # stands for the post-wait screen instead.
            break
        dumped = await _dump(ctx, deadline=deadline)
        if dumped.get("error"):
            # WHY THIS DOES NOT REPORT, where `_wait_until_text`'s same
            # failure does: this returns to a branch that immediately calls
            # `_settle(redump=True)`, whose OWN `_dump` runs against the same
            # device -- a device still failing is reported there as
            # `dump_failed`, and one that has recovered did not deserve a
            # failed step. Graded by
            # `test_a_dump_error_during_a_polled_wait_is_not_lost`.
            return current, False, False
        current = dumped.get("content")
        fresh = True
        if _screen_hash(current) != before:
            return current, True, False
    return current, False, fresh


async def _poll_condition(
    ctx: Context, action: object, spec: tuple[str, str, int], screen: object
) -> tuple[bool, object, str]:
    """Poll one wait condition: ``(found, screen, interruption)``.

    *spec* is ``(kind, wait_text, wait_ms)`` from ``_wait_spec``. The sentence a
    watched interruption left in ``ctx.wait_interruption`` is cleared before the
    poll and read (and cleared) after it.
    """
    kind, wait_text, wait_ms = spec
    ctx.wait_interruption = ""
    if kind == "element":
        found, screen, _timed_out = await _wait_until_element(
            ctx,
            screen,
            wait_text,
            str(getattr(action, "until_rid_text", "") or ""),
            bool(getattr(action, "until_gone", False)),
            wait_ms,
        )
    elif kind == "idle":
        found, screen, _timed_out = await _wait_until_idle(ctx, screen, wait_ms)
    else:
        found, screen, _timed_out = await _wait_until_text(
            ctx, screen, wait_text, wait_ms, gone=kind == "gone"
        )
    interruption, ctx.wait_interruption = ctx.wait_interruption, ""
    return found, screen, interruption


def _condition_words(action: object, kind: str, wait_text: str) -> tuple[str, str, str]:
    """The ``(met, missed, label)`` sentences for a wait condition."""
    if kind != "element":
        return _wait_words(kind, wait_text)
    gone = bool(getattr(action, "until_gone", False))
    label = "element " + repr(wait_text[:120]) + (" gone" if gone else " present")
    return "found " + label, "timed out waiting for " + label, label


async def _condition_wait(
    run: _ReplayRun,
    action: object,
    spec: tuple[str, str, int],
    entry: dict,
    screen: object,
) -> tuple[object, dict | None]:
    """A wait on a text, element or idle condition: ``(screen, reply)``.

    ``reply`` is ``None`` when the condition was met and the replay goes on.
    """
    kind, wait_text, _wait_ms = spec
    op = str(getattr(action, "op", "") or "")
    found, screen, interruption = await _poll_condition(run.ctx, action, spec, screen)
    met, missed, label = _condition_words(action, kind, wait_text)
    if found:
        entry["outcome"] = "ok"
        entry["detail"] = met
        # No re-dump: the poll already left a fresh screen.
        screen, stop = await _settle(
            run.ctx,
            entry,
            screen,
            run.trace,
            entry["index"],
            redump=False,
            mark_no_change=False,
            op=op,
        )
        if stop is not None:
            return screen, {"error": None, "content": stop}
    elif interruption:
        entry["outcome"] = "interrupted"
        entry["detail"] = interruption + " (while " + label + ")"
    else:
        entry["outcome"] = "wait_timeout"
        entry["detail"] = missed
    _stamp_after(entry, screen)
    _append(run.trace, entry)
    if found:
        return screen, None
    return screen, {
        "error": None,
        "content": _result(
            STATUS_NEEDS_MODEL, run.trace, screen, "", entry["detail"], entry["index"]
        ),
    }


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


async def _watch_after_wait(
    ctx: Context, screen: object, before_wait: object, changed: bool, reusable: bool
) -> tuple[object, bool, str]:
    """WATCHED (B6): ``(screen, reusable, interruption)`` after a changed wait.

    A change that is a dialog, a call, another app or an error text is NAMED and
    the replay stops for the model, instead of an "ok" that hands the next
    action a screen the script never expected.

    A FOCUS change returns the pre-wait screen un-read (the probe saw a new
    window, no dump was taken), so on that path the screen is read once here:
    comparing the baseline with itself could never name the call or app that
    took focus. That read IS the post-wait screen, so the settle reuses it
    rather than dumping a second time.

    A dump that fails here is left to ``_settle``, which reports it as
    ``dump_failed`` unless the device names a disconnect.
    """
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


def _interrupted_stop(
    run: _ReplayRun, entry: dict, screen: object, interruption: str
) -> dict:
    """The reply for a changed wait that a watched interruption cut short."""
    entry["outcome"] = "interrupted"
    entry["detail"] = interruption
    _stamp_after(entry, screen)
    _append(run.trace, entry)
    return {
        "error": None,
        "content": _result(
            STATUS_NEEDS_MODEL,
            run.trace,
            screen,
            "",
            interruption,
            entry["index"],
        ),
    }


def _wait_timeout_stop(run: _ReplayRun, entry: dict, screen: object) -> dict:
    """The reply for a non-`wait` op whose screen never changed.

    The model said the screen would change and it did not: hand it the screen
    rather than run the rest of a script written for a screen that never came.
    The legacy `wait` carries on, as it always has. The caller has already
    stamped the entry.
    """
    entry["outcome"] = "wait_timeout"
    _append(run.trace, entry)
    return {
        "error": None,
        "content": _result(
            STATUS_NEEDS_MODEL,
            run.trace,
            screen,
            "",
            entry["detail"],
            entry["index"],
        ),
    }


async def _changed_wait(
    run: _ReplayRun, op: str, wait_ms: int, entry: dict, screen: object
) -> tuple[object, dict | None]:
    """A wait for the screen to change: ``(screen, reply)``.

    ``reply`` is ``None`` when the replay goes on to the next action.
    """
    before_wait = screen
    screen, changed, reusable = await _wait_until_changed(run.ctx, screen, wait_ms)
    entry["outcome"] = "ok"
    entry["detail"] = _changed_detail(op, wait_ms, changed)
    # `redump=not reusable`: only a wait whose LAST poll dumped the unchanged
    # screen hands back a post-wait screen (`_wait_until_changed` says when).
    # Every other path holds a screen from before the wait ended, and skipping
    # the dump there would hand the next action a screen older than the wait
    # itself -- the defect `_settle`'s docstring records.
    screen, reusable, interruption = await _watch_after_wait(
        run.ctx, screen, before_wait, changed, reusable
    )
    if interruption:
        return screen, _interrupted_stop(run, entry, screen, interruption)
    if reusable:
        step_timing.mark("dump_reused")
    screen, stop = await _settle(
        run.ctx,
        entry,
        screen,
        run.trace,
        entry["index"],
        redump=not reusable,
        mark_no_change=False,
        op=op,
    )
    if stop is not None:
        return screen, {"error": None, "content": stop}
    _stamp_after(entry, screen)
    if op != "wait" and not changed:
        return screen, _wait_timeout_stop(run, entry, screen)
    _append(run.trace, entry)
    return screen, None
