"""The lane-OFF note of the qa-doctor mobile section: what an attached device
adds when nothing here drives it. Ships in every edition (no lane import)."""

from __future__ import annotations

import asyncio
import logging

from config.settings_guard import never_raise
from handlers import _lane, _reply, mobile_gate

logger = logging.getLogger("tools.mcp_handlers")


async def _off_lane_serials() -> list[str]:
    """The serials adb reports right now; [] when the probe fails or says nothing."""
    with never_raise(
        logger, "qa-doctor off-lane device note skipped", exc_info=True
    ) as g:
        attached = await asyncio.wait_for(
            _lane.adb.devices(), timeout=_reply.MOBILE_PROBE_S
        )
    if g.failed:
        return []
    if attached.get("error") or attached.get("content") is None:
        return []
    return [str(s) for s in (attached.get("content") or [])]


async def mobile_off_attached_note() -> list:
    """What the lane-OFF disclosure adds when a device is ALREADY visible.

    Empty unless an adb server is already running, so an install with the lane
    off and no adb server gets exactly the text it gets today and this function
    makes no subprocess call whatsoever.
    """
    if not mobile_gate._adb_server_already_running():
        return []
    serials = await _off_lane_serials()
    if not serials:
        return []
    return [
        "- \u2139\ufe0f "
        + str(len(serials))
        + " Android device(s) are attached right now ("
        + ", ".join(serials[:4])
        + "). Nothing here is driving them while this lane is off, and nothing "
        "was started to find them.",
    ]
