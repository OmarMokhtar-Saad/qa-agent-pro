"""Part of the mobile_run split (see handlers/mobile/run.py): boot."""

from __future__ import annotations

import tools.mobile.session_device as session_device
from tools.mobile import emulator, sdk_locator
from tools.mobile import render as mobile_render
from tools.mobile.session_device import DeviceWant


def _avd_choice_pointer(wanted: str, avds: list) -> str:
    """The pointer reply when the AVD choice is unknown or ambiguous, else ``""``."""
    if wanted and wanted not in avds:
        return (
            "## That emulator (AVD) does not exist on this machine\n\n"
            "Call `qa_mobile_test` again with `avd` set to one of: "
            + ", ".join("`" + name + "`" for name in avds)
        )
    if not wanted and len(avds) > 1:
        menu = "\n".join(str(i + 1) + ". `" + name + "`" for i, name in enumerate(avds))
        return (
            "## Several emulators (AVDs) are configured\n\n"
            + menu
            + "\n\nCall `qa_mobile_test` again with `avd` set to the one you want."
        )
    return ""


# Notes for ``_device_stage_boot`` (moved out of the body to keep it short):
# Nothing booted at all. This server downloads no SDK and makes no AVD unasked
# (docs/RETIRED_CAPABILITIES.md -> 6), so a machine without them gets the
# setup guide, and only an AVD the TESTER created can be booted. The one
# question the guide turns on -- is there an emulator binary -- is asked of
# the same `locate_sdk` every other SDK consumer asks.
#
# THE ONLY BRANCH THAT SPAWNS, so the only one that can set the language
# from the first frame. Every branch above ADOPTED a device somebody else
# booted; `_mobile_locale_stage` handles those by reading back.
async def _boot_avds() -> tuple[str, list[str]]:
    """``(reply, avds)``: the setup guide or error to send back, else the tester's AVDs."""

    located = (sdk_locator.locate_sdk() or {}).get("content") or {}
    if not str((located.get("tools") or {}).get("emulator") or ""):
        guide = mobile_render.setup_guide(mobile_render.SETUP_NO_SDK)
        return mobile_render.setup_guide_block(guide), []
    listed = await emulator.list_avds()
    if listed.get("error"):
        return "⚠️ " + str(listed["error"])[:300], []
    avds = [str(name) for name in (listed.get("content") or []) if str(name)]
    if not avds:
        guide = mobile_render.setup_guide(mobile_render.SETUP_NO_AVD)
        return mobile_render.setup_guide_block(guide), []
    return "", avds


async def _boot_spawn(wanted: str, apply: bool, locale: str) -> tuple:
    """Boot the chosen AVD (the only branch that spawns); ``(reply, serial)``."""

    if not apply:
        refusal = mobile_render.apply_refusal(
            "Starting the " + wanted + " emulator", "one detached process"
        )
        return refusal, ""
    ready = await session_device.ensure_device(
        DeviceWant(avd=wanted, locale=str(locale or ""))
    )
    if ready.get("error"):
        return "⚠️ " + str(ready["error"])[:300], ""
    state = ready.get("content") or {}
    picked_serial = str(state.get("serial") or "")
    if str(state.get("state") or "") != "ready":
        return mobile_render.device_pending_block(state), picked_serial
    return "", picked_serial


async def _device_stage_boot(avd: str, apply: bool, locale: str) -> tuple:
    """Nothing is booted: show the setup guide, or boot the tester's own AVD."""
    reply, avds = await _boot_avds()
    if reply:
        return reply, ""
    wanted = str(avd or "").strip()
    pointer = _avd_choice_pointer(wanted, avds)
    if pointer:
        return pointer, ""
    return await _boot_spawn(wanted or avds[0], apply, locale)
