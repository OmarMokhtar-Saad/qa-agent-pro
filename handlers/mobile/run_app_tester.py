"""The ``app_tester`` source of ``qa_mobile_test``: open App Tester, pick the app."""

from __future__ import annotations

from handlers.mobile.run_request import _AppCall, _mobile_ask
from tools.mobile import adb as mobile_adb
from tools.mobile.install_source import InstallOutcome
from tools.mobile.install_sources.app_tester_ui import AppTesterUiSource
from tools.mobile.install_sources.app_tester_ui_pick import ListedApp
from tools.mobile.render_install import (
    APP_TESTER_HINT,
    APP_TESTER_PACKAGES,
    APP_TESTER_STORE_PACKAGE,
)

_SELECTED = (
    "## Opened in App Tester\n\nSelected the app in the list. Use its Download or "
    "Update button, finish in the system installer, then call `qa_mobile_test` "
    "again with the app's `package` name and `source=installed_package`. "
    "Nothing was downloaded by this server.\n\n"
)


async def installed_tester(serial: str) -> str:
    """The App Tester package id installed on the device (current id first), or ``""``."""
    listing = await mobile_adb.installed_packages(serial)
    have = listing.get("content") or []
    return next((p for p in reversed(APP_TESTER_PACKAGES) if p in have), "")


async def _open_tester_store(serial: str) -> str:
    """Open the Play Store page of App Tester under its current package id."""
    url = "market://details?id=" + APP_TESTER_STORE_PACKAGE
    opened = await mobile_adb.open_url(serial, url)
    if opened.get("error"):
        return "⚠️ " + str(opened["error"])[:300]
    return (
        "## Opened on the emulator\n\nApp Tester is not installed. Install it in "
        "the emulator's own UI, then call `qa_mobile_test` again with "
        "`source=app_tester`. Nothing was downloaded by this server."
    )


def _outcome_markdown(outcome: InstallOutcome) -> str:
    if outcome.ok:
        return _SELECTED + APP_TESTER_HINT
    return "⚠️ " + outcome.message


async def app_tester_stage(call: _AppCall, target: str) -> tuple:
    """Launch the installed App Tester, scan its whole list, select the app named
    by the hint. Store page only when App Tester itself is missing."""
    tester = await installed_tester(call.serial)
    if not tester:
        return await _open_tester_store(call.serial), target
    want = ListedApp(call.serial, call.value or target, tester)
    outcome = await AppTesterUiSource().open_listed_app(want, _mobile_ask(call.choose))
    return _outcome_markdown(outcome), target
