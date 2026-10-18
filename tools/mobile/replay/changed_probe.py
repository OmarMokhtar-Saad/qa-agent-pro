"""Replay waits: wait for the screen to change on the cheap probe, dump on motion.

``wait_until_changed`` used to dump every poll. Here the screen probe (focused
window + frame md5) is polled instead; a real dump is taken only when it moves,
and once at the end for an unmoved screen (the confirming read, which also
covers a change that landed between the screen in hand and the first probe).
Every unusable-probe path ends the wait the old way, by dumping. After a dump
that showed the old screen the new baseline must be bracketed by that dump's
own "before" reading, else a change landing during the dump would be absorbed
into the baseline and reported "unchanged".
"""

from __future__ import annotations

import time
from typing import NamedTuple

from tools.mobile import dump_latency, screen_anchor, screen_probe
from tools.mobile.replay.context import Context
from tools.mobile.replay.dump import _dump
from tools.mobile.replay.fresh_read import dump_known_fresh, fresh_read
from tools.mobile.replay.probe_gate import PROBE_BUDGET_S, wait_moved
from tools.mobile.replay.screen import _screen_hash, system_dialog_package
from tools.mobile.replay.waits_early import check_horizon
from tools.mobile.replay.waits_prior import _spinning


class Plan(NamedTuple):
    """What a probed change wait runs on: the baseline and the deadline."""

    base: screen_probe.Reading
    deadline: float


def _focus_changed(base: screen_probe.Reading, now: screen_probe.Reading) -> bool:
    return bool(base.focus and now.focus and base.focus != now.focus)


async def _reusable(ctx: Context, screen: object, base: screen_probe.Reading) -> bool:
    """True when the changed screen is no spinner and focus still equals the baseline's."""
    if not base.focus or _spinning(screen):
        return False
    # Focus only: the frame is not needed to know the window did not move.
    return await screen_probe.focus(ctx.serial, PROBE_BUDGET_S) == base.focus


async def _still_shown(ctx: Context, screen: object) -> bool:
    """True when *screen* is the last dump's, nothing actuated since, and the frame
    still equals the probe that bracketed that dump: it cannot have changed."""
    shown = _screen_hash(screen)
    base = await screen_anchor.trusted_baseline(ctx.serial, shown, PROBE_BUDGET_S)
    return base is not None


async def _agreed(ctx: Context, screen: object, plan: Plan) -> tuple:
    """A dump equal to the baseline: ``(screen, changed, reusable)`` after one fresh read.

    Skipped when the dump came from a service that clears its node cache."""
    if dump_known_fresh(ctx.serial):
        return screen, False, True
    return await fresh_read(ctx, screen, plan.deadline)


async def _confirm(ctx: Context, screen: object, plan: Plan) -> tuple:
    """The confirming read of an unmoved wait: ``(screen, changed, reusable)``.

    "Unchanged" is said only after a fresh read agreed: one that dropped every
    held dump (see :mod:`fresh_read`), unless the dump just taken came from a
    service that clears its own node cache.
    """
    if await _still_shown(ctx, screen):
        return await fresh_read(ctx, screen, plan.deadline)
    dumped = await _dump(ctx, deadline=plan.deadline)
    if dumped.get("error"):
        return screen, False, False
    content = dumped.get("content")
    if _screen_hash(content) != _screen_hash(screen):
        return content, True, await _reusable(ctx, content, plan.base)
    return await _agreed(ctx, content, plan)


async def _rebaseline(ctx: Context, shown: object) -> tuple:
    """``(baseline, bracketed)``: the baseline after a dump that showed the old screen: the bracketed one,
    else a plain probe (the frame moved during the dump), so the wait keeps
    gating on the probe instead of ending in a confirming dump; a change that
    landed in that window is caught by the confirm at the deadline. ``None``
    only when the probe is unavailable. *bracketed* False means the dump in hand
    cannot stand for the post-wait screen."""
    trusted = await screen_anchor.trusted_baseline(
        ctx.serial, _screen_hash(shown), PROBE_BUDGET_S
    )
    if trusted:
        return trusted, True
    return await screen_probe.baseline(ctx.serial, PROBE_BUDGET_S), False


async def _on_move(ctx: Context, plan: Plan, now: object) -> tuple:
    """A moved frame: ``(screen, outcome)``. *outcome* is ``None`` to keep
    waiting, else ``(changed, reusable)`` and *screen* is ``None`` (no new dump)."""
    if _focus_changed(plan.base, now):
        return None, (True, False)
    dumped = await _dump(ctx, deadline=plan.deadline)
    if dumped.get("error"):
        return None, (False, False)
    return dumped.get("content"), None


async def _recheck(ctx: Context, plan: Plan) -> object:
    """A real dump taken while the probe stayed quiet: its screen, or ``None`` (retry).

    A failed or cut dump is no evidence of "no change": the wait goes on and
    asks again, it never ends on it.
    """
    dumped = await _dump(ctx, deadline=plan.deadline)
    return None if dumped.get("error") else dumped.get("content")


async def _next_screen(ctx: Context, plan: Plan, until: float) -> tuple:
    """``(screen, outcome, kind)``: the next screen to judge, kind ``move``,
    ``check`` or ``over``.

    *screen* is a dump when the frame moved, or (qa-ime a11y reader, cheap
    dumps) when ``CHANGED_CHECK_S`` of quiet passed: a change the frame probe
    cannot see (text under the pixel tolerance) ends the wait at the next check
    instead of at the cap. *outcome* is ``None`` to keep waiting.
    """
    horizon = check_horizon(ctx.serial, until)
    moved = await wait_moved(ctx, plan.base, horizon)
    if moved is None and horizon >= until:
        return None, None, "over"
    if moved is None:
        return await _recheck(ctx, plan), None, "check"
    got, outcome = await _on_move(ctx, plan, moved)
    return got, outcome, "move"


async def _verdict(ctx: Context, current: object, seen: tuple) -> object:
    """``(screen, changed, reusable)`` when *current* ends the wait, else ``None``."""
    before, base = seen
    if _screen_hash(current) != before:
        return current, True, await _reusable(ctx, current, base)
    if system_dialog_package(current):
        return current, False, False
    return None


async def _finish(ctx: Context, current: object, held: tuple) -> tuple:
    """The answer once the loop ended: agreed on a bracketed baseline, else confirmed."""
    bracketed, plan = held
    if bracketed:
        return await _agreed(ctx, current, plan)
    return await _confirm(ctx, current, plan)


async def probed_changed(ctx: Context, screen: object, plan: Plan) -> tuple:
    """``(screen, changed, reusable)`` like ``_wait_until_changed``, probe-driven."""
    before, base, current, fresh = _screen_hash(screen), plan.base, screen, False
    until = plan.deadline - (dump_latency.p90_ms(ctx.serial) or 0) / 1000.0
    while time.monotonic() < until:
        got, outcome, kind = await _next_screen(ctx, Plan(base, plan.deadline), until)
        if kind == "over":
            break
        if got is None and kind == "move":
            return (current, *outcome)
        if got is None:
            continue
        current, fresh = got, True
        if ended := await _verdict(ctx, current, (before, base)):
            return ended
        if kind == "move":
            base, fresh = await _rebaseline(ctx, current)
        if base is None:
            break
    return await _finish(ctx, current, (fresh and base, plan))
