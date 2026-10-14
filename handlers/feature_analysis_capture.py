"""The Feature Analysis capture step: pick a device and capture its screens."""

from __future__ import annotations

from handlers import _common, _elicit, capture
from handlers.feature_analysis_types import FeatureAnalysisCall, _FaCapture


async def _fa_pick_device_id(
    device_id: str, choose: _common.ChooseCb
) -> tuple[str, str]:
    """The device id to capture from: ``(device_id, reply)``."""
    device_id = (device_id or "").strip()
    if device_id:
        return device_id, ""
    picked = await _elicit._elicit_device(choose)
    if picked.status == _common.CHOSEN:
        return picked.value or "", ""
    if picked.status == _common.DECLINED:
        return "", "👍 Cancelled — no device selected."
    return "", await capture._device_menu_markdown(tool="qa_feature_analysis")


async def _fa_capture_run(device, call: FeatureAnalysisCall) -> tuple[_FaCapture, str]:
    """Capture the screens of a resolved device: ``(capture, reply)``."""
    screens, capture_error = await capture._fa_capture_screens(
        capture._ShotLoop(device, choose=call.choose, progress=call.progress)
    )
    if capture_error and not screens:
        return _FaCapture(), f"⚠️ Couldn't capture a screenshot: {capture_error}"
    if screens:
        # NO server vision call: the raw screens ride to the tester's own
        # multimodal model as MCP image content, exactly as qa_capture_screens
        # already returns them and as IMAGE_JOB does on the generation path.
        await _common.emit(
            call.progress,
            f"🖼️ Attaching {len(screens)} captured screen(s) for your model…",
        )
    return _FaCapture(len(screens), tuple(screens)), ""


async def _fa_capture_stage(call: FeatureAnalysisCall) -> tuple[_FaCapture, str]:
    """Capture the device screens on the mobile modes: ``(capture, reply)``."""
    mode = call.mode
    if mode not in ("mobile", "jira_mobile"):
        return _FaCapture(), ""
    if not _common.mobile_capture():
        if mode == "mobile":
            return _FaCapture(), "ℹ️ Mobile capture is disabled in this build."
        return _FaCapture(), ""  # jira_mobile continues from the ticket alone
    device_id, reply = await _fa_pick_device_id(call.device_id, call.choose)
    if reply:
        return _FaCapture(), reply
    device, why_not = await _common._resolve_device_with_reason(device_id)
    if device is None:
        return _FaCapture(), (
            f"⚠️ Device `{device_id}` not found. Run qa_list_devices to "
            "see connected devices, then pass a listed id." + why_not
        )
    return await _fa_capture_run(device, call)
