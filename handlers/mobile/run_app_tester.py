"""The ``app_tester`` source of ``qa_mobile_test``: find the app in App Tester, install it."""

from __future__ import annotations

import logging
from typing import Optional

from handlers import _common
from handlers.mobile import run_skipped
from handlers.mobile.run_request import _AppCall, _mobile_ask
from tools.mobile import adb as mobile_adb
from tools.mobile import (
    app_alias,
    app_stage_pick,
    dump_latency_scope,
    resolve_cache,
    run_store,
)
from tools.mobile.app_label_from_goal import goal_app_label
from tools.mobile.install_source import (
    ALREADY_LATEST,
    InstallOutcome,
    InstallRequest,
)
from tools.mobile.install_sources.app_tester_ui import AppTesterUiSource
from tools.mobile.install_sources.app_tester_ui_handoff import host_next
from tools.mobile.install_sources.app_tester_ui_labels import default_label, newest_note
from tools.mobile.install_sources.app_tester_ui_labels import refusal as label_refusal
from tools.mobile.install_sources.app_tester_ui_memory import (
    known_bundle,
    parse_app_id,
)
from tools.mobile.install_sources.app_tester_ui_pick import ListedApp
from tools.mobile.render_install import (
    APP_TESTER_HINT,
    APP_TESTER_PACKAGES,
    APP_TESTER_STORE_PACKAGE,
)

logger = logging.getLogger(__name__)

_INSTALLING = (
    "Installing the app through App Tester: its Download button, then the "
    "system installer."
)

_SCANNING = "Reading the App Tester list to find the app."

_ALREADY_LATEST = (
    "The installed app already is the latest build App Tester listed: nothing to "
    "download."
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


def _failed(outcome: InstallOutcome, want: Optional[ListedApp] = None) -> str:
    """The stop reply; after a device step, the host's own way on."""
    after = ("\n\n" + host_next(want.serial, want.tester)) if want else ""
    return "⚠️ " + outcome.message + "\n\n" + APP_TESTER_HINT + after


async def _install_listed(call: _AppCall, want: ListedApp) -> tuple:
    """Find the app's row, then Download and install it: ``("", package)`` once
    the installed version is confirmed, else the outcome message."""
    source = AppTesterUiSource()
    if want.package and await source.is_latest(call.serial, want.package):
        await _common.emit(call.progress, _ALREADY_LATEST)
        run_skipped.mark_tester_install(call.serial, want.package, False)
        return "", want.package
    if not want.package:
        logger.info("app_tester: no known package for the label: scanning the list")
    ask = _mobile_ask(call.choose)
    await _common.emit(call.progress, _SCANNING)
    bundle = await source.find_listed_app(want, ask)
    if isinstance(bundle, InstallOutcome):
        return _failed(bundle, want), ""
    return await _install_found(call, want, (source, ask, bundle))


async def _install_found(call: _AppCall, want: ListedApp, found: tuple):
    """Install the listed *bundle* unless the device already holds its newest
    build (the package was unknown, so the first short-circuit was missed)."""
    source, ask, bundle = found
    if bundle != want.package and await source.is_proven_latest(call.serial, bundle):
        await _common.emit(call.progress, _ALREADY_LATEST)
        app_alias.remember_alias(call.serial, want.label, bundle)
        run_skipped.mark_tester_install(call.serial, bundle, False)
        return "", bundle
    await _common.emit(call.progress, _INSTALLING)
    request = InstallRequest(
        call.serial, bundle, want.label, latest=True, launch=call.launch
    )
    outcome = await source.install(request, ask)
    if not outcome.ok:
        return _failed(outcome, want), bundle
    app_alias.remember_alias(call.serial, want.label, bundle)
    run_skipped.mark_tester_install(call.serial, bundle, outcome.code != ALREADY_LATEST)
    run_skipped.mark_tester_evidence(call.serial, outcome.evidence)
    return "", bundle


async def known_package(serial: str, label: str) -> str:
    """The package this label is already known to name on this device: the
    resolved-app cache first, then the one installed app the label fits (no
    question is asked here), then the package an earlier App Tester scan
    matched to this label, if it is installed. ``""`` when none knows. Only a
    hint for the list scan: a package the list does not hold changes nothing."""
    key = resolve_cache.cache_key(serial, label)
    cached = run_store.read_resolved_app(key) if label else {}
    if cached.get("package"):
        return str(cached["package"])
    picked = (await app_stage_pick.pick_installed(serial, label)).package
    return picked or await _listed_and_installed(serial, label)


async def _listed_and_installed(serial: str, label: str) -> str:
    package = known_bundle(serial, label)
    if not package:
        return ""
    have = (await mobile_adb.installed_packages(serial)).get("content") or []
    return package if package in have else ""


def _hint_label(call: _AppCall, target: str) -> str:
    """The app label the call names; the device's one remembered label only
    when no link or app id picks the app instead."""
    if parse_app_id(call.value):
        return goal_app_label(call.goal) or ""
    return (
        call.value or target or goal_app_label(call.goal) or default_label(call.serial)
    )


async def _echo_default(call: _AppCall, target: str, label: str) -> None:
    """Say which remembered app was taken when the call named none (B07/B08).

    ``package=`` (*target*) names the app too, so it is never reported as a default.
    """
    named = call.value or target or goal_app_label(call.goal)
    note = newest_note(call.serial) if label and not named else ""
    if note:
        await _common.emit(call.progress, note)


async def app_tester_stage(call: _AppCall, target: str) -> tuple:
    """Launch the installed App Tester, find the app named by the hint in its
    list, install it (Download + system installer) and continue the run with
    its package. Store page only when App Tester itself is missing."""
    tester = await installed_tester(call.serial)
    if not tester:
        return await _open_tester_store(call.serial), target
    label = _hint_label(call, target)
    await _echo_default(call, target, label)
    known = await known_package(call.serial, label)
    if not label and not known:  # nothing names the app: ask before any launch
        return _failed(label_refusal(call.serial)), ""
    link = call.source_text
    want = ListedApp(call.serial, label, tester, known, link)
    try:
        return await _install_listed(call, want)
    finally:  # its slow uiautomator dumps must not become the run's p90
        dump_latency_scope.forget_slow(call.serial)
