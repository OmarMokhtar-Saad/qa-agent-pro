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
from tools.mobile.replay.probe_gate import PROBE_BUDGET_S, wait_moved
from tools.mobile.replay.screen import _screen_hash
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
    now = await screen_probe.read(ctx.serial, PROBE_BUDGET_S)
    return now is not None and now.focus == base.focus


async def _still_shown(ctx: Context, screen: object) -> bool:
    """True when *screen* is the last dump's, nothing actuated since, and the frame
    still equals the probe that bracketed that dump: it cannot have changed."""
    shown = _screen_hash(screen)
    base = await screen_anchor.trusted_baseline(ctx.serial, shown, PROBE_BUDGET_S)
    return base is not None


async def _confirm(ctx: Context, screen: object, plan: Plan) -> tuple:
    """The confirming read of an unmoved wait: ``(screen, changed, reusable)``."""
    if await _still_shown(ctx, screen):
        return screen, False, True
    dumped = await _dump(ctx, deadline=plan.deadline)
    if dumped.get("error"):
        return screen, False, False
    content = dumped.get("content")
    if _screen_hash(content) == _screen_hash(screen):
        return content, False, True
    return content, True, await _reusable(ctx, content, plan.base)


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


async def probed_changed(ctx: Context, screen: object, plan: Plan) -> tuple:
    """``(screen, changed, reusable)`` like ``_wait_until_changed``, probe-driven."""
    before, base = _screen_hash(screen), plan.base
    until = plan.deadline - dump_latency.p90_ms(ctx.serial) / 1000.0
    current, fresh = screen, False
    while time.monotonic() < until and (moved := await wait_moved(ctx, base, until)):
        got, outcome = await _on_move(ctx, Plan(base, plan.deadline), moved)
        if got is None:
            return (current, *outcome)
        current, fresh = got, True
        if _screen_hash(current) != before:
            return current, True, await _reusable(ctx, current, base)
        base, fresh = await _rebaseline(ctx, current)
        if base is None:
            break
    if fresh and base:
        return current, False, True
    return await _confirm(ctx, current, plan)
