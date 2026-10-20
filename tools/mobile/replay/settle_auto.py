"""Replay settle: after an action whose first read showed no change, wait on the event clock."""

from __future__ import annotations

import time
from typing import Any

from tools.mobile import a11y_events, dump_latency, latency_scale
from tools.mobile.replay.context import Context
from tools.mobile.replay.context_consts import AUTO_SETTLE_MAX_S
from tools.mobile.replay.waits_gen import Settled, settle


def _settle_deadline(ctx: Context) -> float:
    """The wall-clock cut for one auto-settle, scaled to the device's dump latency."""
    return time.monotonic() + latency_scale.scale_settle(
        AUTO_SETTLE_MAX_S, dump_latency.p90_ms(ctx.serial)
    )


# Never raises and never fails the step: a dump error returns the screen the
# caller already had, and the caller reports ``no_change`` as before.
# ``before_hash`` is the baseline when the device sent no event clock.
async def _auto_settle(ctx: Context, screen: Any, before_hash: str) -> Any:
    """After an action whose first read showed no change: settle, read once."""
    return (await _auto_settle_seen(ctx, screen, before_hash)).tree


# What the caller needs to tell "the tap did nothing" from "the screen moved
# and no tree could show it": a settle that SAW the screen move while every
# tree stayed the same is NOT proof of no change (run 8f3169's stale a11y
# tree), so the caller reports it as unverified rather than ``no_change``.
async def _auto_settle_seen(ctx: Context, screen: Any, before_hash: str) -> Settled:
    """:func:`_auto_settle`, also saying whether the screen moved or a read failed."""
    base = a11y_events.latest(ctx.serial) or before_hash
    got = await settle(ctx, base, _settle_deadline(ctx))
    return got._replace(tree=screen if got.tree is None else got.tree)
