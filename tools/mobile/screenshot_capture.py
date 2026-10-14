"""Capturing the screen: raw-and-scale on a heavy panel, else ``adb.screencap``."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from tools.mobile import adb, avd_guard
from tools.mobile.screenshot_resize import png_from_raw

logger = logging.getLogger("tools.mobile.screenshot")

#: The shortest wait worth a screencap retry, in seconds. Below it a second
#: try is unlikely to answer where a 30 s one did not.
MIN_RETRY_TIMEOUT_S = 8
#: Seconds of the turn's budget kept back from the retry for the rest of the
#: packet (render, send).
RETRY_MARGIN_S = 2


def retry_timeout_s(remaining: float | None, full_s: float) -> float | None:
    """The wait for the ONE screencap retry, or ``None`` when there is none.

    ``remaining`` is the turn budget's time left (``None``: no budget, no
    retry). ``RETRY_MARGIN_S`` is kept back for the rest of the packet, and a
    wait shorter than ``MIN_RETRY_TIMEOUT_S`` is not worth trying; the wait
    never exceeds ``full_s``, the first try's own timeout.
    """
    if remaining is None:
        return None
    left = float(remaining) - RETRY_MARGIN_S
    if left < MIN_RETRY_TIMEOUT_S:
        return None
    return min(float(full_s), left)


#
# On a panel whose long edge is over ``avd_guard.HEAVY_AVD_MAX_LONG_EDGE_PX``
# the frame is pulled RAW and scaled here (:func:`png_from_raw`). A raw
# timeout is returned as it is, ``timed_out`` included -- the PNG path would
# only time out again, and the packet's retry and audit status read that
# field -- and every other failure falls back to ``adb.screencap`` unchanged,
# including whatever that reports. ``timeout_s`` goes to whichever capture
# runs, clamped there. Never raises.
async def capture(serial: str, *, timeout_s: float | None = None) -> dict:
    """The screen as PNG bytes, ``{"error", "content"}`` -- raw on a heavy panel."""

    with never_raise(
        logger, "mobile.screenshot.capture raw path failed", exc_info=True
    ):
        sized = await adb.display_size(serial)
        edge = max((sized or {}).get("content") or [0, 0])
        if int(edge) > avd_guard.HEAVY_AVD_MAX_LONG_EDGE_PX:
            raw = await adb.screencap_raw(serial, timeout_s=timeout_s)
            if (raw or {}).get("timed_out"):
                return raw
            png = png_from_raw((raw or {}).get("content"))
            if png:
                return {"error": None, "content": png}
    if timeout_s is None:
        return await adb.screencap(serial)
    return await adb.screencap(serial, timeout_s=timeout_s)
