"""The ``app_tester`` source of ``qa_mobile_test``: find the app in App Tester, install it."""

from __future__ import annotations

from handlers import _common
from handlers.mobile.run_request import _AppCall, _mobile_ask
from tools.mobile import adb as mobile_adb
from tools.mobile import app_alias, app_stage_pick, resolve_cache, run_store
from tools.mobile.install_source import InstallOutcome, InstallRequest
from tools.mobile.install_sources.app_tester_ui import AppTesterUiSource
from tools.mobile.install_sources.app_tester_ui_pick import ListedApp
from tools.mobile.render_install import (
    APP_TESTER_HINT,
    APP_TESTER_PACKAGES,
    APP_TESTER_STORE_PACKAGE,
)

_INSTALLING = (
    "Installing the app through App Tester: its Download button, then the "
    "system installer."
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


def _failed(outcome: InstallOutcome) -> str:
    return "⚠️ " + outcome.message + "\n\n" + APP_TESTER_HINT


async def _install_listed(call: _AppCall, want: ListedApp) -> tuple:
    """Find the app's row, then Download and install it: ``("", package)`` once
    the installed version is confirmed, else the outcome message."""
    source = AppTesterUiSource()
    ask = _mobile_ask(call.choose)
    bundle = await source.find_listed_app(want, ask)
    if isinstance(bundle, InstallOutcome):
        return _failed(bundle), ""
    await _common.emit(call.progress, _INSTALLING)
    request = InstallRequest(call.serial, bundle, want.label, latest=True)
    outcome = await source.install(request, ask)
    if not outcome.ok:
        return _failed(outcome), bundle
    app_alias.remember_alias(call.serial, want.label, bundle)
    return "", bundle


async def known_package(serial: str, label: str) -> str:
    """The package this label is already known to name on this device: the
    resolved-app cache first, then the one installed app the label fits (no
    question is asked here). ``""`` when neither knows. Only a hint for the
    list scan: a package the list does not hold changes nothing."""
    key = resolve_cache.cache_key(serial, label)
    cached = run_store.read_resolved_app(key) if label else {}
    if cached.get("package"):
        return str(cached["package"])
    return (await app_stage_pick.pick_installed(serial, label)).package


async def app_tester_stage(call: _AppCall, target: str) -> tuple:
    """Launch the installed App Tester, find the app named by the hint in its
    list, install it (Download + system installer) and continue the run with
    its package. Store page only when App Tester itself is missing."""
    tester = await installed_tester(call.serial)
    if not tester:
        return await _open_tester_store(call.serial), target
    label = call.value or target
    known = await known_package(call.serial, label)
    link = call.source_text
    want = ListedApp(call.serial, label, tester, known, link)
    return await _install_listed(call, want)
