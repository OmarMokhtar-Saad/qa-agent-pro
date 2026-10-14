"""Device-row normalising and the bounded Android probes (moved from handlers._reply)."""

from __future__ import annotations

import asyncio
import logging
import time

from config.settings_guard import never_raise
from handlers import _common, _lane_tools

logger = logging.getLogger("tools.mcp_handlers")


#: What a device row that is not a dict becomes. It is NOT dropped: the audit
#: count and the rendered listing both follow the row count, so a silent drop
#: makes both under-report what `device_manager` produced.
_UNREADABLE_ROW = {
    "id": "",
    "name": "unreadable device row",
    "platform": "",
    "kind": "",
}


def normalize_rows(devices: list) -> list:
    """COPIES of *devices*, one row out per row in (see the design notes)."""
    return [
        dict(dev) if isinstance(dev, dict) else dict(_UNREADABLE_ROW)
        for dev in (devices or [])
    ]


async def _probe_row(row: dict, budget: float, mods: tuple) -> None:
    """Fill ``avd`` / ``api`` into one admitted Android *row*; a failure leaves it."""
    mobile_adb, mobile_emulator = mods
    serial = str(row.get("id") or "")
    with never_raise(
        logger,
        "device detail probe failed for one device",
        level=logging.INFO,
        exc_info=True,
    ):
        if str(row.get("kind") or "") == "emulator":
            named = await asyncio.wait_for(
                mobile_emulator.avd_name_of(serial), timeout=budget
            )
            row["avd"] = str((named or {}).get("content") or "")[:60]
        facts = await asyncio.wait_for(mobile_adb.device_facts(serial), timeout=budget)
        row["api"] = str(((facts or {}).get("content") or {}).get("api") or "")[:8]


def _is_android(row: dict) -> bool:
    """Whether *row* is an Android device with a serial to probe."""
    return bool(row.get("id")) and str(row.get("platform") or "") == "android"


def _mobile_probe_modules() -> tuple | None:
    """The (adb, emulator) modules, or None when the mobile tools are absent."""
    if not _common.mobile_modules_present():
        return None
    return (_lane_tools.adb, _lane_tools.emulator)


async def probe_rows(rows: list, budget: float) -> list:
    """*rows* with ``avd`` / ``api`` filled in for Android rows. Never raises.

    Rationale and the probe-budget bound: the design notes in
    ``handlers._reply``."""
    mods = _mobile_probe_modules() if rows else None
    if mods is None:
        return rows
    deadline = time.monotonic() + budget
    for row in rows:
        # The one gate, and it is per ROW: see the docstring. An admitted row
        # keeps its probes' own bounds; a listing whose budget is gone stops
        # asking.
        if _is_android(row) and time.monotonic() < deadline:
            await _probe_row(row, budget, mods)
    return rows
