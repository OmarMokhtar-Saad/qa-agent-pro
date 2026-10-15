"""Replay raw dumps: one uiautomator dump, through the selected tree source when allowed."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from tools.mobile import (
    adb,
    dump_latency,
    dump_reuse,
    latency_scale,
    step_timing,
    tree_optin,
    tree_source,
)

logger = logging.getLogger("tools.mobile.executor")


async def dump_raw(serial: str, *, use_provider: bool = True) -> dict:
    """ONE raw ``uiautomator dump`` for a caller outside :func:`_dump`.

    Session open, the case lane's foreground loop and the explore turn read
    the raw dump themselves; the a11y parity check passes ``use_provider=False``
    because it needs uiautomator's own answer, not the fast dump it is judging. Calling ``adb.uiautomator_dump`` directly would
    start a second dump beside one :func:`_dump` left running after a cut, on
    the same ``adb.DUMP_REMOTE_PATH`` (fix round 3, item 4). This takes the
    same guard: join the dump in flight, start this one through
    ``dump_latency.start`` (so its real time feeds the p90) and await it
    shielded, so a caller's own ``wait_for`` cancels the wait, never the dump.
    """
    await dump_latency.join(serial)
    task = dump_latency.start(
        serial, adb.uiautomator_dump(serial, use_provider=use_provider)
    )
    return await step_timing.timed("ui_dump", asyncio.shield(task))


async def dump_raw_reusing(serial: str) -> dict:
    """:func:`dump_raw`, but serve the replay's last dump when it is provably current.

    Only for the read that FOLLOWS a replay (the explore turn's packet): a
    polling loop must never use it, since it exists to see the screen change.
    Nothing actuated on the device since that dump (see
    :mod:`tools.mobile.dump_reuse`) and it is younger than its age bound, or
    this takes a real dump.
    """
    held = dump_reuse.take(serial)
    if held is not None:
        step_timing.mark("dump_reused_raw")
        return held
    return await dump_raw(serial)


#: What a dump cut at a wait's deadline reports. An error result, because the
#: polled wait already treats any failed poll as "stop polling"; the dump
#: itself was NOT cancelled and finishes on the device (see `_dump`).
DUMP_CUT_DETAIL = (
    "The screen dump did not finish before the wait's deadline; it was left "
    "to finish on the device."
)


#: What a tree source may fail with. CancelledError is not here.
SOURCE_FAILURES = (OSError, ValueError, RuntimeError, asyncio.TimeoutError)


async def _pick_source(serial: str) -> Any:
    try:
        return await tree_source.select_tree_source(serial, tree_optin.allowed)
    except SOURCE_FAILURES:
        logger.warning("mobile.executor: tree source selection failed", exc_info=True)
        return None


# ONE raw dump through the selected tree source; the adb dump on any miss.
#
# Only a source ``tree_optin.allowed`` lets through is ever tried, so without
# the tester's consent this is exactly the adb dump. A source that returns
# nothing or raises falls back to the adb dump in the same call.
async def _raw_dump(serial: str) -> dict:
    """ONE raw dump through the selected tree source; the adb dump on any miss."""
    source = await _pick_source(serial)
    if source is not None and source.name != tree_source.FALLBACK_SOURCE:
        timeout = latency_scale.scale_timeout(
            latency_scale.TREE_TIMEOUT_S, dump_latency.p90_ms(serial)
        )
        try:
            result = await source.dump(serial, timeout)
        except SOURCE_FAILURES:
            logger.warning("mobile.executor: tree source failed", exc_info=True)
            result = None
        if result is not None and result.xml:
            step_timing.note_source(result.source)
            return {"error": None, "content": result.xml}
    step_timing.note_source(tree_source.FALLBACK_SOURCE)
    return await adb.uiautomator_dump(serial)
