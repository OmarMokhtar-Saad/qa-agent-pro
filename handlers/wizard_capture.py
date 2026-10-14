"""Wizard screen capture for the mobile sources: device pick plus the capture."""

from __future__ import annotations

from handlers import _common, _elicit, capture
from handlers.wizard_request import WizardIO


def _capture_off_reply(src: str) -> str | None:
    if src == "mobile":
        return "ℹ️ Mobile capture is disabled in this build."
    # jira_mobile continues from the ticket alone (parity with the
    # Chainlit wizard's capture-off fallback).
    return None


# K1 (2026-08-10): this is dialog #3-4 of a qa_wizard call, so a
# spent budget is its TYPICAL unanswered state, not an edge case.
# The old text claimed no devices were connected -- but
# _elicit_device only reaches a dialog AFTER list_devices returned
# some, so that was simply untrue whenever the tester just did not
# answer.
async def _pick_device(io: WizardIO) -> tuple[str | None, str]:
    """(refusal or None, the picked device value)."""
    picked_dev = await _elicit._elicit_device(io.choose)
    if picked_dev.status == _common.DECLINED:
        return "👍 Cancelled — no device selected.", ""
    if _common.unanswered(picked_dev):
        return await capture._device_menu_markdown("qa_wizard"), ""
    if picked_dev.status != _common.CHOSEN:
        return (
            "⚠️ No connected devices found. Attach a device or boot an "
            "emulator/simulator, then try again (check with "
            "`qa_list_devices`).",
            "",
        )
    return None, picked_dev.value or ""


async def _guided_capture_images(src: str, io: WizardIO) -> tuple[str | None, list]:
    """Capture screens for the mobile sources; (refusal or None, images)."""
    if src not in ("mobile", "jira_mobile"):
        return None, []
    if not _common.mobile_capture():
        return _capture_off_reply(src), []
    refusal, picked = await _pick_device(io)
    if refusal is not None:
        return refusal, []
    device = await _common.resolve_device(picked)
    if device is None:
        return "⚠️ Device not found — run qa_list_devices and retry.", []
    screens, capture_error = await capture._fa_capture_screens(
        capture._ShotLoop(device, choose=io.choose, progress=io.progress)
    )
    if capture_error and not screens:
        return f"⚠️ Couldn't capture a screenshot: {capture_error}", []
    return None, screens
