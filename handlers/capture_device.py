"""The device picker of ``qa_capture_screens``: the rescan menu, the markdown
fallback list and the device resolution that precedes a capture."""

from __future__ import annotations

from typing import NamedTuple

from handlers import _common, _elicit, _reply
from tools.device_manager import list_devices

_RESCAN_LABEL = "🔄 Rescan for devices"


async def _ask_round(choose: _common.ChooseCb, rounds: int) -> tuple:
    """``(picked, by_label)`` for one picker round; a rescan bypasses the cache."""
    # A rescan must bypass the discovery TTL cache: the tester just plugged
    # a phone in or booted an emulator.
    result = await list_devices(refresh=rounds > 1)
    devices = result.get("content") or []
    labels, by_label = _elicit._device_options(devices)
    picked = await _elicit.elicit_choice(
        choose, "Which device?", [*labels, _RESCAN_LABEL]
    )
    return picked, by_label


def _device_result(picked: _common.ChoiceResult, by_label: dict):
    """The ``ChoiceResult`` for a non-rescan answer to the picker."""
    if picked.status == _common.CHOSEN:
        device_id = by_label.get(picked.value or "")
        if device_id:
            return _common.ChoiceResult(_common.CHOSEN, device_id)
        return _common.ChoiceResult(_common.UNAVAILABLE)
    return picked


# Device picker WITH an explicit rescan option (used by qa_capture_screens).
# A tester who plugs the phone in after the picker appeared -- or boots an
# emulator -- otherwise has no way forward but re-calling the tool, so the menu
# carries a "Rescan for devices" entry and re-scans in place, bounded by
# _MAX_ELICIT_ROUNDS. _elicit_device itself is deliberately left byte-identical
# so the wizard / Maestro / Feature-Analysis callers keep today's behaviour.
# Never raises: any transport failure degrades to UNAVAILABLE and the caller
# falls back to the markdown device list.
async def _elicit_device_with_rescan(
    choose: _common.ChooseCb, progress: _common.ProgressCb = None
) -> _common.ChoiceResult:
    """Device picker WITH an explicit rescan option (used by qa_capture_screens)."""
    rounds = 0
    while rounds < _common._MAX_ELICIT_ROUNDS:
        rounds += 1
        picked, by_label = await _ask_round(choose, rounds)
        if picked.status == _common.CHOSEN and picked.value == _RESCAN_LABEL:
            await _common.emit(progress, "🔄 Rescanning for devices…")
            continue
        return _device_result(picked, by_label)
    return _common.ChoiceResult(_common.UNAVAILABLE)


async def _device_menu_markdown(tool: str) -> str:
    """Markdown fallback for the device picker: the live device list plus a
    re-call instruction for *tool*."""
    result = await list_devices()
    # The SAME rows as `qa_list_devices`. One renderer with two behaviours would
    # be two meanings wearing one name, and "which emulator is this?" is exactly
    # the question a picker exists to answer.
    devices = await _reply.android_device_details(result.get("content") or [])
    # The unusable rows travel with the picker too: a tester whose phone is not
    # in the choices is owed the reason IN THE PLACE THEY ARE CHOOSING, not
    # only in `qa_list_devices`.
    # The truncation count travels with the PICKER too: a tester whose phone is
    # not among the choices is owed the reason in the place they are choosing.
    return _reply.shape_devices(
        _reply.DeviceListing(
            devices, result.get("unusable") or [], result.get("adb_dropped_lines") or 0
        ),
    ) + (f"\n\nRe-call `{tool}` with the chosen `device_id`.")


class _PickReq(NamedTuple):
    """The arguments of ``_pick_capture_device`` (the tool's own device args)."""

    device_id: str
    rescan: bool
    choose: _common.ChooseCb
    progress: _common.ProgressCb


async def _pick_by_id(device_id: str) -> tuple:
    """``(device, None)`` for a known id, else ``(None, early_reply)``."""
    device, why_not = await _common._resolve_device_with_reason(device_id)
    if device is None:
        return None, (
            f"⚠️ Device `{device_id}` not found. Run `qa_list_devices` "
            "and retry with an id from that list." + why_not,
            [],
        )
    return device, None


async def _pick_by_menu(
    choose: _common.ChooseCb, progress: _common.ProgressCb
) -> tuple:
    """``(device, None)`` once the picker yields one, else the markdown menu reply."""
    picked = await _elicit_device_with_rescan(choose, progress)
    device = None
    if picked.status == _common.CHOSEN and (picked.value or "").strip():
        device = await _common.resolve_device(picked.value or "")
    if device is None:
        return None, (await _device_menu_markdown("qa_capture_screens"), [])
    return device, None


async def _pick_capture_device(req: _PickReq) -> tuple:
    """``(device, None)`` once a device is chosen, else ``(None, early_reply)``."""
    device_id = (req.device_id or "").strip()
    if device_id and not req.rescan:
        return await _pick_by_id(device_id)
    return await _pick_by_menu(req.choose, req.progress)
