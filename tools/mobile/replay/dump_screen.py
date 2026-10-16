"""Replay screen reads: the pruned, observed screen and the activity its identity is hashed with."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from tools.mobile import (
    adb,
    dump_cut_keep,
    dump_latency,
    dump_reuse,
    step_timing,
)
from tools.mobile.providers import composite
from tools.mobile.replay.context_ctx import Context
from tools.mobile.replay.dump_carry import _remember, _take_carried
from tools.mobile.replay.dump_raw import DUMP_CUT_DETAIL, _raw_dump

logger = logging.getLogger("tools.mobile.executor")


async def _first_screen(ctx: Context) -> dict:
    """The first read of a call: the carried screen if valid, else a dump."""
    carried = await _take_carried(ctx)
    if carried is not None:
        return carried
    return await _dump(ctx)


# Re-dump and re-prune. Returns the ``{"error","content"}`` shape.
#
# SINGLE-FLIGHT PER SERIAL (fix round 3, item 4). Every dump writes the same
# ``adb.DUMP_REMOTE_PATH``, so this first joins any dump still running on
# the device and only then starts its own, through ``dump_latency.start``,
# which also records the dump's real time for the adaptive margin.
#
# With a *deadline* (an instant on this module's ``time.monotonic``) the
# wait is cut there -- and ONLY the wait: ``asyncio.shield`` leaves the adb
# dump running to its end, where it records its time and the next ``_dump``
# joins it. A cut returns :data:`DUMP_CUT_DETAIL` as an error.
async def _dump(ctx: Context, deadline: float | None = None) -> dict:
    """Re-dump and re-prune. Returns the ``{"error","content"}`` shape."""
    raw = await _fetched(ctx.serial, deadline)
    if raw.get("error"):
        return raw
    return await _observed(ctx, raw)


def _cut() -> dict:
    step_timing.mark("dump_cut")
    return {"error": DUMP_CUT_DETAIL, "content": None}


async def _fetched(serial: str, deadline: float | None) -> dict:
    if deadline is None:
        await dump_latency.join(serial)
    elif not await dump_latency.join(serial, deadline - time.monotonic()):
        return _cut()
    kept = dump_cut_keep.take(serial)
    if kept is not None:
        step_timing.mark("dump_cut_served")
        return kept
    under = dump_reuse.token(serial)
    task = dump_latency.start(serial, _raw_dump(serial))
    try:
        raw = await _awaited(task, deadline)
    except asyncio.TimeoutError:
        dump_cut_keep.keep_when_done(serial, under, task)
        return _cut()
    dump_reuse.store(serial, under, raw)
    return raw


async def _awaited(task: asyncio.Future, deadline: float | None) -> dict:
    if deadline is None:
        return await step_timing.timed("ui_dump", asyncio.shield(task))
    wait = max(0.0, deadline - time.monotonic())
    return await step_timing.timed(
        "ui_dump", asyncio.wait_for(asyncio.shield(task), wait)
    )


async def _observed(ctx: Context, raw: dict) -> dict:
    # The display is a DEVICE fact, so it is read from the device rather than
    # inferred from the dump; `display_size` caches it per serial and never
    # refuses, so this costs one round trip per session and cannot fail a dump.
    # Cached per serial like the size, so the accessibility floor is this
    # device's 48dp. The fresh focus probe runs beside them, not after them.
    sized, dpi, activity = await asyncio.gather(
        adb.display_size(ctx.serial),
        adb.display_density(ctx.serial),
        resolve_activity(ctx),
    )
    observed = composite.observe(
        raw.get("content"),
        activity,
        composite.ScreenView(sized.get("content"), dpi.get("content")),
    )
    _remember(ctx, observed)
    return observed


# THE activity a screen's identity is hashed with. One producer, no other.
#
# A caller that already knows the activity keeps priority: this only fills the
# gap that made `screen_id` hash two dimensions instead of three. Read fresh on
# every dump (one grep'd focus line, short timeout, run beside the other device
# reads); a failed probe is "unknown" and is simply asked again on the next dump.
#
# PUBLIC, and named without the underscore, because the prune sites OUTSIDE
# this module must ask it rather than answer the question themselves.
# `perception._screen_id` hashes ``package|activity|texts[:3]``, so a site
# that reads the raw ``ctx.activity`` attribute stores a screen under a
# DIFFERENT id from the one this module stamps on the trace -- and the report
# joins exactly those two. Measured: run ``mrun-20260909-202601-5d6cf0``
# stored three screens as ``54746b60347d`` while its trace named
# ``d86961ab9d42``, with identical content hashes proving one dump behind
# both, and shipped a report with three PNGs on disk and no picture under any
# step. ``session.context_for`` builds a fresh context per MCP call with
# ``activity=""``, so a packet-build site never inherits the replay's probe;
# the two answers cannot converge by luck.
#
# *ctx* is typed ``object`` and read through ``getattr``: `explore_runner`
# takes its context duck-typed, and a producer that only accepts one concrete
# class is a producer half the callers cannot use.
async def resolve_activity(ctx: Any) -> str:
    """THE activity a screen's identity is hashed with."""
    known = str(getattr(ctx, "activity", "") or "")
    if known:
        return known
    # Read fresh on every dump (fix/1210): a per-context memo froze the activity
    # under a screen that later changed it. The key no longer hashes it, so the
    # probe is metadata and an empty answer is normal.
    serial = _serial_for_activity(ctx)
    probed = await adb.current_activity(serial)
    return str(probed.get("content") or "")


def _serial_for_activity(ctx: Any) -> str:
    """The context's serial, warning when it has none."""
    serial = str(getattr(ctx, "serial", "") or "")
    if not serial:
        # Not a refusal -- an empty activity is a NORMAL answer here and a
        # weaker screen identity beats a refused dump. But it is the one way
        # this producer can quietly hand back the two-dimensional id it exists
        # to prevent, so it says so rather than looking like a probe that found
        # nothing on a real device.
        logger.warning(
            "mobile.executor.resolve_activity: no serial on the context; "
            "screen ids will be hashed without an activity"
        )
    return serial
