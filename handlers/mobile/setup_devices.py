"""Attached-device and device-lock rows of the qa-doctor mobile section (moved from setup_check)."""

from __future__ import annotations

import asyncio
import logging

from config.settings_guard import never_raise
from handlers import _reply
from tools.mobile import adb as mobile_adb
from tools.mobile import locks as mobile_locks

logger = logging.getLogger("tools.mcp_handlers")


async def _mobile_attached_devices() -> dict:
    """Ask adb which devices are attached; a slow probe is not an absent device.

    THE DEVICE LOCKS, ONE PER DEVICE, ASKED BY NAME: the design's honest bound
    (plan §5.6) is that a holder whose heartbeat writer never started is NOT
    force-released, and the cost of that choice is disclosed instead."""

    with never_raise() as g:
        return await asyncio.wait_for(
            mobile_adb.devices(), timeout=_reply.MOBILE_PROBE_S
        )
    return {
        "error": "the device probe did not finish (" + str(g.exc)[:80] + ")",
        "content": None,
    }


def _lock_error_line(where: str, row: dict) -> str:
    return (
        "- ⚠️ Could not check whether a run holds "
        + where
        + ": "
        + str(row.get("error"))[:160]
    )


def _lock_stuck_line(where: str, row: dict) -> str:
    return (
        "- ⚠️ "
        + where
        + " has been held by pid "
        + str(row.get("pid") or "?")
        + " (run `"
        + str(row.get("owner") or "?")
        + "`) for "
        + str(int(row.get("age") or 0))
        + "s. Nothing here will break that hold — a lock taken "
        "from a live holder means two runs on one device. If "
        "that process is wedged, quit the editor holding it."
    )


def _lock_busy_line(where: str, row: dict) -> str:
    return (
        "- ✅ "
        + where
        + " is in use by run `"
        + str(row.get("owner") or "?")
        + "` — one run drives a device at a time, and the other "
        "devices below are unaffected"
    )


def _mobile_lock_row_line(row: dict) -> str:
    """One doctor line for one device's lock row."""
    where = str(row.get("serial") or "?")
    if row.get("error"):
        return _lock_error_line(where, row)
    if row.get("held_too_long"):
        return _lock_stuck_line(where, row)
    if row.get("held"):
        return _lock_busy_line(where, row)
    return "- ✅ " + where + " is free"


def _mobile_lock_rows(attached: dict) -> list:
    """The device-lock lines, ONE PER DEVICE.

    IT MUST NAME THE DEVICE: a probe with no argument reads a lock nobody takes
    and reports "free" while a device is being driven."""

    if attached.get("error") or attached.get("content") is None:
        # FAILS CLOSED: "no run holds a device" would be a guess, and the wrong one.
        return [
            "- ⚠️ Could not check which devices are attached, so nothing "
            "here says whether a run holds one: "
            + str(attached.get("error") or "the probe failed")[:160]
        ]
    serials = [str(s) for s in (attached.get("content") or [])]
    lines = [] if serials else ["- ⬜ No device is attached, so no run holds one"]
    lines += [_mobile_lock_row_line(r) for r in mobile_locks.device_holders(serials)]
    return lines
