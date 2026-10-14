"""``qa_list_devices``: the handler plus the never-raise probes that explain an
empty list and the reply built from a listing result. It ships in every
edition, so it lives outside ``handlers.mobile`` (the lane package)."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from handlers import _common, _lane, _reply
from handlers._boundary import handler_guard
from tools.device_manager import list_devices
from tools.error_boundary import text_reply

logger = logging.getLogger("tools.mcp_handlers")


# Early in a boot adb does not list the emulator at all, so the only
# evidence is that this server started it (fix round 3, item 5).
def _booting_devices(devices: list, unusable: list) -> list:
    """Emulators this server started that adb does not list yet."""
    booting: list = []
    if not devices and not unusable:
        with never_raise(logger, "booting probe failed", exc_info=True):
            booting = _lane.emulator.recently_started()
    return booting


# B3: an empty list is explained (AVDs on file + a bounded acceleration
# check), never by the minute-long WHPX feature query.
async def _empty_devices_hint(devices: list, unusable: list, booting: list) -> str:
    """Why the list is empty, when nothing is listed, unusable or booting."""
    empty_hint = ""
    if not devices and not unusable and not booting:
        with never_raise(logger, "no-devices diagnosis failed", exc_info=True):
            empty_hint = _lane.emulator.render_no_devices_hint(
                await _lane.emulator.diagnose_no_devices()
            )
    return empty_hint


async def _listing_reply(result: dict) -> str:
    """The device-listing reply for a successful ``list_devices`` *result*."""
    devices = await _reply.android_device_details(result.get("content") or [])
    unusable = result.get("unusable") or []
    await _common.audit(
        "mcp_list_devices",
        detail={"count": len(devices), "unusable": len(unusable)},
    )
    booting = _booting_devices(devices, unusable)
    empty_hint = await _empty_devices_hint(devices, unusable, booting)
    return _reply.shape_devices(
        _reply.DeviceListing(
            devices,
            unusable,
            result.get("adb_dropped_lines") or 0,
            booting=booting,
            empty_hint=empty_hint,
            adb_problem=str(result.get("adb_problem") or ""),
        ),
    )


@handler_guard(
    "qa_list_devices",
    "handle_list_devices failed",
    text_reply("\u26a0\ufe0f Device discovery failed: "),
)
async def handle_list_devices(*, progress: _common.ProgressCb = None) -> str:
    await _common.emit(progress, "\U0001f4f1 Discovering connected devices\u2026")
    # The tester asked for a scan: never answer it from the TTL cache.
    result = await list_devices(refresh=True)
    if result.get("error"):
        return f"\u26a0\ufe0f Device discovery failed: {result['error']}"
    return await _listing_reply(result)
