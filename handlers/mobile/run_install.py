"""Part of the mobile_run split (see handlers/mobile/run.py): install."""

from __future__ import annotations

import logging

import tools.mobile.session_device as session_device
from config.settings_guard import never_raise
from handlers import _common
from handlers.mobile.run_app_tester import app_tester_stage
from handlers.mobile.run_request import _AppCall
from tools import device_manager as _device_manager
from tools.mobile import adb as mobile_adb
from tools.mobile import prerun_timing
from tools.mobile import run_store as _run_store

logger = logging.getLogger("tools.mcp_handlers")

_APK_NEEDS_PACKAGE = (
    "⚠️ Give the app's `package` name as well as the .apk path: it "
    "is what the preflight and every relaunch check, and this "
    "server cannot read it out of the file. Nothing was installed.",
    "",
)


async def _installed_names(serial: str) -> list[str]:
    """The third-party package ids installed on the device."""

    installed = await _device_manager.list_installed_apps(
        {"id": serial, "platform": "android"}
    )
    return [
        str(app.get("id") or "")
        for app in (installed.get("content") or [])
        if isinstance(app, dict) and app.get("id")
    ]


def _last_used_hint(serial: str) -> str:
    """The "last used" hint line, or empty."""

    last = _run_store.last_used_package(serial)
    return ("\n\nLast used on this device: `" + last + "`") if last else ""


async def _not_installed_hint(serial: str, suggestions: list[str]) -> str:
    """Whatever hints the device can give about the missing app."""
    hint = (
        (" Closest installed match: `" + "`, `".join(suggestions) + "`.")
        if suggestions
        else ""
    )
    with never_raise(logger, "not-installed hint lookup failed", exc_info=True):
        names = await _installed_names(serial)
        if names:
            hint += "\n\nInstalled third-party packages: " + ", ".join(names[:20])
        hint += _last_used_hint(serial)
    return hint


async def _mobile_not_installed_reply(
    serial: str, target: str, suggestions: list[str]
) -> str:
    """The "not installed" reply, with whatever hints the device can give."""
    hint = await _not_installed_hint(serial, suggestions)
    return (
        "⚠️ `"
        + (target or "(none given)")
        + "` is not installed on the emulator, so there is nothing to run."
        + hint
        + "\n\nPick another install source, or install it and try again."
    )


# Notes for ``_app_stage_not_installed``:
# The SECOND absence assertion on this path, and it is reachable with an
# unanswered probe exactly as the menu was. Same rule: only a probe that
# answered may say the app is not there.
async def _app_stage_not_installed(call: _AppCall, target: str, probe: tuple) -> tuple:
    """The ``installed_package`` source found no such app: say so, with hints."""
    probed, suggestions = probe
    if not probed:
        return (
            "⚠️ Could not check whether `"
            + (target or "(none given)")
            + "` is on the emulator -- the device did not answer, so this "
            "server will not say either way. Check the emulator is still "
            "running (`qa_list_devices`) and call `qa_mobile_test` again. "
            "Nothing was installed or started.",
            target,
        )
    reply = await _mobile_not_installed_reply(call.serial, target, suggestions)
    return reply, target


def _store_url(install_source: str, value: str, target: str) -> str:
    """The market URL to open for a Play-Store style install source."""
    url = value
    if install_source == "play_store":
        url = (
            value
            if value.startswith("market://")
            else "market://details?id=" + (value or target)
        )
    return url


def _store_opened_markdown() -> str:
    """The reply once the store page is open on the emulator."""

    return (
        "## Opened on the emulator\n\nFinish the install in the emulator's own "
        "UI, then call `qa_mobile_test` again with the app's `package` name and "
        "`source=installed_package`. Nothing was downloaded by this server."
    )


async def _app_stage_open_store(
    call: _AppCall, install_source: str, target: str
) -> tuple:
    """Open the Play Store page, or drive Firebase App Tester, in the emulator."""

    if install_source == "app_tester":
        return await prerun_timing.timed(
            call.serial, "app_tester", app_tester_stage(call, target)
        )
    url = _store_url(install_source, call.value, target)
    opened = await mobile_adb.open_url(call.serial, url)
    if opened.get("error"):
        return "⚠️ " + str(opened["error"])[:300], target
    return _store_opened_markdown(), target


def _installing_reply(target: str) -> tuple:
    """The reply once the outside-the-call install has started."""
    return (
        "## Installing `"
        + target
        + "`\n\nThe install runs outside this call (a large APK takes "
        "longer than a tool call may last). Call `qa_mobile_status`; when "
        "the package shows as installed, call `qa_mobile_test` again.",
        target,
    )


async def _local_apk_stage(call: _AppCall, target: str) -> tuple:
    """The ``local_apk`` source: start the install outside this call."""

    if not target:
        return _APK_NEEDS_PACKAGE
    await _common.emit(call.progress, "\U0001f4e6 Starting the install…")
    started = session_device.start_install(call.serial, call.value, target)
    if started.get("error"):
        return "⚠️ " + str(started["error"])[:300], target
    return _installing_reply(target)
