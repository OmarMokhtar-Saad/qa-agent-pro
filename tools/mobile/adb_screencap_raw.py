"""Raw screen capture: ``screencap_raw`` and its verdict (moved from adb.py)."""

from __future__ import annotations

import asyncio
import logging

from tools import tool_status
from tools.mobile import adb_run
from tools.mobile.adb_host import host
from tools.mobile.adb_reply import clamp_timeout, refuse_device_id
from tools.mobile.adb_screencap import _adb_missing

logger = logging.getLogger("tools.mobile.adb")


def _raw_timed_out(wait_s: float) -> dict:
    """A raw capture that timed out, marked like the PNG path (fix round 3, item 3)."""
    # A heavy panel takes this path first, and an unmarked timeout here was
    # logged `ok` by `_tracked`.
    tool_status.mark_timed_out("screencap_raw")
    return {
        "error": "raw screencap did not answer within " + str(wait_s) + "s.",
        "timed_out": True,
        "content": None,
    }


# :func:`screencap`'s contract and reasons (``run_argv`` directly,
# ``exec-out``, never raises) without ``-p``: the device skips its own PNG
# encode of a full-size frame, which on a heavy panel is the slow part, and
# ``screenshot.png_from_raw`` scales BEFORE it encodes. Fix round 3, item 7.
#
# ``timeout_s`` is clamped exactly as :func:`screencap` clamps it (never above
# :data:`DEFAULT_TIMEOUT_S`, never below one second), and a timeout returns
# the same ``timed_out`` shape as :func:`raw`, so the packet's shorter retry
# and its audit status (fix round 3, item 3) hold on the raw path too.
async def screencap_raw(serial: str, *, timeout_s: float | None = None) -> dict:
    """The screen as a RAW ``screencap`` frame: ``{"error", "content": bytes|None}``."""
    if not host()._valid_device_id(serial):
        return refuse_device_id(serial)
    wait_s = clamp_timeout(timeout_s)
    try:
        cmd = [host().resolve_adb(), "-s", str(serial), "exec-out", "screencap"]
        rc, out, err = await adb_run.run_argv(cmd, wait_s)
    except FileNotFoundError:
        return _adb_missing()
    except asyncio.TimeoutError:
        return _raw_timed_out(wait_s)
    except Exception as exc:
        logger.exception("mobile.adb.screencap_raw failed")
        return {"error": str(exc), "content": None}
    return _screencap_raw_verdict(rc, out, err)


def _screencap_raw_verdict(rc: int, out: bytes, err: bytes) -> dict:
    """The answer for a finished raw ``screencap``: the frame, or why not."""
    if rc != 0:
        said = " ".join(err.decode(errors="replace").split())[:200]
        return {
            "error": (
                "raw screencap exited " + str(rc) + ((": " + said) if said else ".")
            ),
            "content": None,
        }
    if len(out) > host().MAX_RAW_SCREENSHOT_BYTES:
        return {
            "error": (
                "This screen's raw frame exceeds the "
                + str(host().MAX_RAW_SCREENSHOT_BYTES)
                + " byte cap and was discarded."
            ),
            "content": None,
        }
    return {"error": None, "content": out}
