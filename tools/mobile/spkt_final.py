"""Mobile session: the final-packet capture. Cut verbatim from tools/mobile/session_packets.py (C5 w17)."""

from __future__ import annotations

import asyncio
import logging

from config.settings_guard import never_raise
from tools.mobile import adb
from tools.mobile.providers import composite
from tools.mobile.replay.dump import dump_raw

logger = logging.getLogger("tools.mobile.session")


async def _capture(serial: str) -> dict | None:
    dumped = await dump_raw(serial)
    if dumped.get("error"):
        return None
    sized = await adb.display_size(serial)
    dpi = await adb.display_density(serial)
    pruned = composite.observe(
        dumped.get("content"),
        "",
        composite.ScreenView(sized.get("content"), dpi.get("content")),
    )
    if pruned.get("error"):
        return None
    return pruned.get("content") or None


# One best-effort screen capture spent from the reserved tail of a busy
# reply. Never raises -- a device that will not answer in time degrades the
# busy reply to plain text, exactly as before this change.
#
# Reuses the SAME primitive ``case_runner.start_case`` uses to prune a fresh
# dump into a screen (``adb.uiautomator_dump``/``display_size``/
# ``display_density`` feeding :func:`composite.observe`), with no relaunch,
# no foreground poll and no activity resolve -- those are the slow parts
# this fix exists to avoid starting this close to the client's own timeout.
#
# The whole capture runs under ``asyncio.wait_for`` with what is left of
# *budget* as its timeout, so a device that stalls on any of the three adb
# calls is cut off at the budget deadline and the call never overruns
# :data:`CALL_BUDGET_S`.
async def _final_packet(ctx: object, budget: object) -> dict | None:
    serial = str(getattr(ctx, "serial", "") or "")
    remaining = getattr(budget, "remaining", None)
    if not serial or not callable(remaining):
        return None
    with never_raise(
        logger,
        "mobile.session: _final_packet capture failed",
        level=logging.WARNING,
        exc_info=True,
    ):
        try:
            return await asyncio.wait_for(
                _capture(serial), timeout=max(0.0, remaining())
            )
        except asyncio.TimeoutError:
            logger.info("mobile.session: _final_packet capture hit the call budget")
            return None
    return None
