"""Capture 1..N device screens for test-case grounding: ``(markdown, specs)``.

REUSES the existing capability instead of duplicating it:
_elicit_device_with_rescan for the picker, _fa_capture_screens for the
capture loop, device_manager.capture_screenshot underneath. The markdown
names every capture_id and its label plus the exact
``qa_prepare_test_cases(capture_ids=[...])`` re-call; *specs* are the
{filename, mime, data} dicts the tool layer turns into MCP image content, so
the tester's OWN multimodal model sees the pixels immediately. The bytes are
ALSO stashed in the tray for the prepare call. At most _CAPTURE_COUNT_MAX
screens per call, and a clamped request is DISCLOSED rather than silently
trimmed.

Gated on the mobile_capture() seam, which is hardcoded True since
2026-08-13 (QA_MOBILE_CAPTURE deleted, pinned to the `true` the dist
.env.example already shipped), so this tool is LIVE on every edition.
Deliberately does NOT refuse in the test-cases-only edition (unlike
handle_feature_analysis): tools/device_manager IS
shipped there, capturing app screens grounds test-case generation -- that
edition's one job -- and this makes no server-side vision call, so the
credential-free promise holds (see docs/FEATURE_FLAGS.md). Never raises."""

from __future__ import annotations

from typing import NamedTuple

from handlers import _common, _elicit
from handlers import capture_screens as _screens
from handlers._boundary import handler_guard
from handlers.capture_device import _pick_capture_device, _PickReq
from handlers.capture_labels import _capture_labels
from handlers.capture_peek import _capture_peek
from handlers.capture_reply import (
    _capture_specs,
    _capture_tray_notes,
    _captured_reply,
    _ReplyParts,
    _stash_with_accounting,
    _TrayNotes,
)
from handlers.capture_screens import _capture_want, _ShotLoop


class _CaptureCall(NamedTuple):
    """The arguments of ``qa_capture_screens``."""

    device_id: str
    n_screens: int
    rescan: bool
    names: str
    peek: bool
    choose: _common.ChooseCb
    progress: _common.ProgressCb


class _CaptureReq(NamedTuple):
    """What ``_capture_from_device`` needs once a device is chosen."""

    device: dict
    n_screens: int
    names: str
    choose: _common.ChooseCb
    progress: _common.ProgressCb


async def _capture_dispatch(call: _CaptureCall) -> tuple:
    """Gate, peek, pick a device, capture: ``(markdown, specs)``."""
    if not _common.mobile_capture():
        return _CAPTURE_DISABLED_NOTICE, []
    if call.peek:
        # G2: a text-only look. Before ANY device selection or capture so a
        # peek can never take, stash or attach a screenshot.
        return await _capture_peek(call.device_id), []
    pick = _PickReq(call.device_id, call.rescan, call.choose, call.progress)
    device, early_reply = await _pick_capture_device(pick)
    if early_reply is not None:
        return early_reply
    return await _capture_from_device(
        _CaptureReq(device, call.n_screens, call.names, call.choose, call.progress)
    )


_CAPTURE_DISABLED_NOTICE = (
    "ℹ️ Device screen capture is disabled in this build. You can "
    "still attach the screenshots to this chat instead and pass "
    "`attached_image_count` to `qa_prepare_test_cases`."
)


def _no_screen_reply(capture_error: object) -> tuple:
    """The reply when the device returned no screenshot at all."""
    return (
        "⚠️ Couldn't capture a screenshot: "
        f"{capture_error or 'no image returned'}. Check the device is "
        "unlocked and still connected (`qa_list_devices`).",
        [],
    )


# The attachments come from the TRAY ENTRIES OF THE IDS LISTED, so the headline
# count, the id rows, the names and the attached images are one list. Building
# them from `screens` attached every offered image, 12 beside a headline that
# said 10. The label shelf is consumed here, so this must stay AFTER the
# `not screens` exit of the caller.
async def _report_capture(device: dict, screens: list, notes: tuple) -> tuple:
    """Stash *screens*, audit and word the reply; *notes* is (names, error, clamp)."""
    names, capture_error, clamp_note = notes
    labels, label_note = _capture_labels(names, len(screens))
    ids, dropped, unusable, pushed_out = _stash_with_accounting(screens, labels)
    specs = _capture_specs(ids)
    await _common.audit(
        "mcp_capture_screens",
        detail={"count": len(ids), "device": device.get("id", "")},
    )
    note = _capture_tray_notes(
        _TrayNotes(
            capture_error=capture_error,
            dropped=dropped,
            unusable=unusable,
            pushed_out=pushed_out,
        )
    )
    parts = _ReplyParts(device, ids, label_note, clamp_note + note)
    return _captured_reply(parts), specs


async def _fa_capture_screens(run: _ShotLoop) -> tuple:
    """Call-time hop to ``capture_screens.fa_capture_screens`` (patch seam)."""
    return await _screens.fa_capture_screens(run)


async def _capture_from_device(req: _CaptureReq) -> tuple:
    """Capture from the chosen device, stash and report: ``(markdown, specs)``."""
    want, clamp_note = _capture_want(req.n_screens)
    screens, capture_error = await _fa_capture_screens(
        _ShotLoop(req.device, n_screens=want, choose=req.choose, progress=req.progress)
    )
    if not screens:
        return _no_screen_reply(capture_error)
    notes = (req.names, capture_error, clamp_note)
    return await _report_capture(req.device, screens, notes)


@handler_guard(
    "qa_capture_screens",
    "handle_capture_screens failed",
    lambda exc: (f"⚠️ Screen capture failed: {exc}", []),
)
async def handle_capture_screens(  # noqa: PLR0913 - MCP tool schema
    device_id: str = "",
    count: int = 1,
    rescan: bool = False,
    names: str = "",
    peek: bool = False,
    *,
    choose: _common.ChooseCb = None,
    ask_text: _elicit.AskCb = None,
    progress: _common.ProgressCb = None,
) -> tuple:
    """Capture 1..N device screens for test-case grounding: ``(markdown, specs)``."""
    call = _CaptureCall(device_id, count, rescan, names, peek, choose, progress)
    return await _capture_dispatch(call)
