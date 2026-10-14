"""Part of the mobile_run split (see handlers/mobile/run.py): device."""

from __future__ import annotations

from dataclasses import dataclass

import tools.mobile.session_device as session_device
from handlers import _common, _elicit, mobile_gate
from handlers.mobile.run_boot import _device_stage_boot
from tools.mobile import emulator
from tools.mobile import render as mobile_render
from tools.mobile.session_device import DeviceWant


async def _mobile_ensure_ready(serial: str, avd: str = "") -> tuple:
    """``(markdown_to_send_back, serial)`` for one named device: an empty
    markdown means the device is ready."""

    ready = await session_device.ensure_device(DeviceWant(serial=serial, avd=avd))
    if ready.get("error"):
        return "⚠️ " + str(ready["error"])[:300], ""
    state = ready.get("content") or {}
    picked_serial = str(state.get("serial") or "")
    if str(state.get("state") or "") != "ready":
        return mobile_render.device_pending_block(state), picked_serial
    return "", picked_serial


# Notes for ``_device_stage_pick`` (moved out of the body to keep it short):
# The label came back from the CLIENT, so the serial in it is not
# evidence. Only a serial we actually enumerated may be driven.
async def _device_stage_pick(booted: list, choose: _common.ChooseCb) -> tuple:
    """Several emulators are running: let the tester pick one, then ready it."""

    labels = _pick_labels(booted)
    picked = await _elicit.elicit_choice(
        choose,
        "Several emulators are running. Which one should this run use?",
        labels,
    )
    if picked.status != _common.CHOSEN:
        return _pick_menu_reply(labels), ""
    chosen_serial = str(picked.value or "").split(" ", 1)[0].strip()
    if chosen_serial not in {str(item.get("serial") or "") for item in booted}:
        return _pick_unknown_reply(booted), ""
    return await _mobile_ensure_ready(chosen_serial)


def _pick_labels(booted: list) -> list:
    """One ``serial (avd)`` label per running emulator."""
    return [
        str(item.get("serial") or "")
        + " ("
        + (str(item.get("avd") or "") or "unknown AVD")
        + ")"
        for item in booted
    ]


def _pick_menu_reply(labels: list) -> str:
    """The numbered menu sent back when the tester did not choose."""
    menu = "\n".join(str(i + 1) + ". " + label for i, label in enumerate(labels))
    return (
        "## Several emulators are running\n\n"
        + menu
        + "\n\nCall `qa_mobile_test` again with `serial` set to the "
        "one you want."
    )


def _pick_unknown_reply(booted: list) -> str:
    """The refusal sent back when the chosen serial was never enumerated."""
    return (
        "## That device is not one of the running emulators\n\n"
        "Call `qa_mobile_test` again with `serial` set to one of: "
        + ", ".join("`" + str(i.get("serial") or "") + "`" for i in booted)
    )


@dataclass(frozen=True)
class _DeviceCall:
    """The inputs of one device stage that the selection reads."""

    apply: bool
    serial: str = ""
    avd: str = ""
    locale: str = ""
    choose: _common.ChooseCb = None


# Notes for ``_mobile_device_stage`` (moved out of the body to keep it short):
# BEFORE the device is touched: a serial the tester copied out of
# `qa_list_devices` may name an iOS simulator or phone, which this lane
# cannot drive. Fails open -- see `_mobile_non_android`.
# Notes for ``_mobile_device_stage`` (moved out of the body to keep it short):
#     the tester's own AVD, or the setup guide. Returns
#     ``(markdown_to_send_back, serial)``.
#
#     A tuple rather than a string because the caller needs the serial and asking
#     for it twice would mean two device probes per call -- and, worse, two
#     answers that could disagree.
#
#     Device selection runs FIRST: an explicit ``serial`` or an already-booted
#     emulator means there is nothing to boot. Only when NOTHING is connected
#     does this look for the tester's AVDs, and spawning still needs
#     ``apply=true``.
#
#     Neither the spawn nor a boot-wait is allowed to block this call: the
#     emulator is started and
#     then polled with an explicit bounded budget, so the reply is always a
#     pointer to ``qa_mobile_status`` rather than a tool call the client kills
#     at its own timeout.
#
async def _mobile_device_stage(call: _DeviceCall) -> tuple:
    """Device selection, then -- only if nothing is already booted --
    an explicit serial wins; otherwise look at what is running.
    """
    given_serial = str(call.serial or "").strip()
    if given_serial:
        refusal = await mobile_gate._mobile_non_android(given_serial)
        if refusal:
            return refusal, ""
        return await _mobile_ensure_ready(given_serial, str(call.avd or ""))
    return await _device_stage_running(call)


async def _device_stage_running(call: _DeviceCall) -> tuple:
    """No serial given: ready the one running emulator, pick, or boot."""

    running = await emulator.list_running()
    if running.get("error"):
        return "⚠️ " + str(running["error"])[:300], ""
    booted = running.get("content") or []
    if len(booted) == 1:
        only = booted[0]
        return await _mobile_ensure_ready(str(only.get("serial") or ""))
    if len(booted) > 1:
        return await _device_stage_pick(booted, call.choose)
    return await _device_stage_boot(call.avd, call.apply, call.locale)
