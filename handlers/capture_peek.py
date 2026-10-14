"""``qa_capture_screens(peek=true)``: the current Android screen as text."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from handlers import _common, _lane
from handlers.capture_device import _device_menu_markdown
from tools.device_manager import list_devices

logger = logging.getLogger("tools.mcp_handlers")


async def _peek_by_id(wanted: str) -> tuple:
    """``(device, "")`` for a known id, else ``(None, not_found_reply)``."""
    device, why_not = await _common._resolve_device_with_reason(wanted)
    if device is None:
        return None, (
            f"\u26a0\ufe0f Device `{wanted}` not found. Run `qa_list_devices` "
            "and retry with an id from that list." + why_not
        )
    return device, ""


async def _android_rows() -> list:
    """The listed devices whose platform is android."""
    listed = await list_devices()
    return [
        d
        for d in (listed.get("content") or [])
        if isinstance(d, dict) and d.get("platform") == "android"
    ]


async def _peek_only_android() -> tuple:
    """The one Android device, else the device menu (never a guess)."""
    android = await _android_rows()
    if len(android) != 1:
        return None, await _device_menu_markdown("qa_capture_screens")
    return android[0], ""


async def _peek_find(wanted: str) -> tuple:
    """``(device, "")`` or ``(None, reply)`` for the peek target."""
    if wanted:
        return await _peek_by_id(wanted)
    return await _peek_only_android()


async def _peek_render(device: dict) -> str:
    """The accessibility dump of an Android *device*; iOS is refused by name."""
    if device.get("platform") != "android":
        return (
            "\u2139\ufe0f Screen peek reads the Android accessibility tree, so it "
            "cannot read an iOS device. Call `qa_capture_screens` without "
            "`peek` for a screenshot."
        )
    return await _lane.peek.peek(str(device.get("id") or ""))


# Read-only and light: one accessibility dump through ``tools.mobile.peek``. No
# screenshot, no tray entry, no capture id, no actuation, no picker, so no
# ``apply``. With no id it uses the only Android device and otherwise shows the
# device menu (never a guess); an iOS device is refused by name. Never raises.
async def _capture_peek(device_id: str) -> str:
    """``qa_capture_screens(peek=true)``: the current Android screen as text."""
    with never_raise(logger, "_capture_peek failed", exc_info=True):
        if not _common.mobile_lane_enabled():
            return (
                "\u2139\ufe0f Screen peek is not part of this build. Call "
                "`qa_capture_screens` without `peek` for a screenshot."
            )
        device, early = await _peek_find((device_id or "").strip())
        if device is None:
            return early
        return await _peek_render(device)
    return (
        "\u26a0\ufe0f Could not peek at the screen. Call `qa_capture_screens` "
        "without `peek` for a screenshot."
    )
