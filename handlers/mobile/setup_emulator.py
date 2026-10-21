"""AVD and running-emulator rows of the qa-doctor mobile section (moved from setup_check)."""

from __future__ import annotations

import asyncio
import logging

from config.settings_guard import never_raise
from handlers import _reply
from handlers.mobile import setup_devices
from tools.mobile import avd_guard, emulator, sdk_locator
from tools.mobile import render as doctor_render

logger = logging.getLogger("tools.mcp_handlers")


async def _list_avds(emulator) -> dict:
    # The SAME producer the run's device stage asks. `list_avds` runs its
    # subprocess on a worker thread, so this wait really is the bound.
    with never_raise():
        return await asyncio.wait_for(
            emulator.list_avds(), timeout=_reply.MOBILE_PROBE_S
        )
    return {"error": "the AVD probe did not finish", "content": None}


def _avd_summary_line(avds: dict, names: list, render) -> str:
    if avds.get("error"):
        return "- ⬜ Emulators (AVDs) not checked: " + str(avds["error"])[:160]
    if names:
        return (
            "- ✅ "
            + str(len(names))
            + " emulator(s) configured: "
            + ", ".join("`" + str(n)[:40] + "`" for n in names[:5])
        )
    return (
        "- ⬜ No AVD → create one in Android Studio's Device Manager ("
        + render.AVD_GUIDE_URL
        + "); `qa_mobile_test` answers with the same setup guide"
    )


# Fix round 2, item 7: a heavy AVD is read from its config.ini and
# the host load from the OS, so this needs no booted device.
def _avd_weight_lines(names: list, sdk_locator, avd_guard) -> list:
    lines: list = []
    for name in names[:5]:
        shown = sdk_locator.avd_display(name).get("content") or {}
        heavy = avd_guard.assess(
            avd_guard.Panel(
                shown.get("width", 0), shown.get("height", 0), shown.get("density")
            )
        )
        if heavy:
            lines.append("- ⚠️ `" + str(name)[:40] + "`: " + heavy)
    return lines


async def _physical_attached() -> bool:
    """True when adb lists a usable device that is not an emulator."""
    found = await setup_devices._mobile_attached_devices()
    serials = found.get("content") or []
    return any(not str(x).startswith("emulator-") for x in serials)


async def _mobile_avd_rows() -> list:
    """The AVD lines of the mobile doctor section: configured emulators, heavy
    AVDs and host load."""

    avds = await _list_avds(emulator)
    names = list(avds.get("content") or [])
    lines = [_avd_summary_line(avds, names, doctor_render)]
    lines += _avd_weight_lines(names, sdk_locator, avd_guard)
    loaded = ""
    if not await _physical_attached():
        # The load warning is about AVD speed; on a physical phone it blames
        # an emulator the run is not using (G12).
        loaded = avd_guard.assess(avd_guard.Panel(load_per_core=avd_guard.host_load()))
    if loaded:
        lines.append("- ⚠️ " + loaded)
    return lines


# `provisioner` went with the AVD_NAME filter below: this probe no
# longer asks about ONE hardcoded AVD, so its only two uses in this
# scope are gone and leaving the import would fail ruff (F401).
# EVERY booted emulator, not just ours. `find_running(AVD_NAME)` filters
# to the hardcoded AVD, which is the D1 defect -- and it survived here
# after the device stage was fixed: on 2026-09-04 this line reported
# "No emulator running" while S25_Ultra_API35 was booted on
# emulator-5554 and the lane went on to drive it successfully. Telling a
# tester nothing is running, and that the tool will start one, is the
# message that made them abort the run in the first place.
# "No emulator running" would be a guess, and the wrong one: the
# probe itself failed, so this says nothing about what is booted.
# Includes asyncio.TimeoutError, which IS an Exception on every version
# this project supports. "not checked" is the honest word here.
async def _mobile_emulator_lines() -> list:
    """The line(s) for the emulators adb reports as running."""
    lines: list = []
    with never_raise(logger, "qa-doctor emulator probe skipped", exc_info=True) as g:
        found = await asyncio.wait_for(
            emulator.list_running(), timeout=_reply.MOBILE_PROBE_S
        )
        lines += _emulator_found_lines(found, sdk_locator)
    if g.failed:
        lines.append(
            "- ⬜ Emulator state not checked (the device probe did not answer "
            "in time); `qa_mobile_status` reports it on demand"
        )
    return lines


def _emulator_found_lines(found: dict, sdk_locator) -> list:
    if found.get("error"):
        return ["- ⚠️ Could not ask adb what is running: " + str(found["error"])[:160]]
    booted = found.get("content") or []
    if not booted:
        return ["- ⬜ No emulator running — `qa_mobile_test` starts one"]
    return _running_emulator_rows(booted, sdk_locator)


# The producer the machine report's image row reads, so the
# doctor and the desktop's decrypt opt-in cannot disagree.
def _running_emulator_rows(booted: list, sdk_locator) -> list:
    """One line per booted emulator, plus a hint when there are several."""
    lines: list = []
    for item in booted:
        serial = str((item or {}).get("serial") or "")
        named = str((item or {}).get("avd") or "")
        avd = named or "unknown AVD"
        found = sdk_locator.avd_system_image(named) or {}
        image = str(found.get("content") or "")
        lines.append(
            "- ✅ Emulator `"
            + avd
            + "` is running as "
            + serial
            + (" (" + image + ")" if image else "")
        )
    if len(booted) > 1:
        lines.append("  Pass `serial` to `qa_mobile_test` to say which one to use.")
    return lines
