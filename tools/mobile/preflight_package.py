"""The target-package step of the preflight: is the app on the device."""

from __future__ import annotations

import logging

from tools.device_manager import valid_package_name
from tools.mobile import adb
from tools.mobile import preflight_rows as rows
from tools.mobile.preflight_consts import _STEP_ERRORS
from tools.mobile.preflight_rows import _Dev, _failed, _unanswered

logger = logging.getLogger("tools.mobile.preflight")


def _no_package_row() -> dict:
    """The row when no target package was given."""
    return rows.record_row(
        "package_installed",
        rows._Row(
            False,
            "no target app package was given",
            "Tell the mobile lane which app to test: a local APK "
            "path, a download URL, a Play Store listing, or the "
            "package name of an app already on the device.",
        ),
    )


def _bad_name_row(package: str) -> dict:
    """The row when the package is not a valid Android package name."""
    return rows.record_row(
        "package_installed",
        rows._Row(
            False,
            repr(package[:60]) + " is not a valid Android package name",
            "Use the app's package id, for example com.example.app.",
        ),
    )


def _no_device_row(package: str) -> dict:
    """The row when there is no device to look the package up on."""
    return rows.record_row(
        "package_installed",
        rows._Row(
            False,
            "cannot check " + package + " without a device",
            "Start the emulator first.",
        ),
    )


def _package_refusal(package: str, resolved_serial: str) -> dict | None:
    """The failing row when the package cannot even be looked up, else ``None``."""
    if not package:
        return _no_package_row()
    if not valid_package_name(package):
        return _bad_name_row(package)
    if not resolved_serial:
        return _no_device_row(package)
    return None


async def _package_row(package: str, dev: _Dev) -> dict:
    """The single ``package_installed`` row read from the package list."""
    installed = await (
        dev.early.pop("packages", None) or adb.installed_packages(dev.serial)
    )
    unanswered = _unanswered("package_installed", installed)
    if unanswered:
        return unanswered
    present = package in (installed.get("content") or [])
    detail = package + (" is installed" if present else " is NOT installed")
    fix = (
        ""
        if present
        else "Install the app first: give the mobile lane an "
        "APK path or a download URL, or open its Play Store "
        "listing on the device."
    )
    return rows.record_row("package_installed", rows._Row(present, detail, fix))


async def _step_package_installed(
    checks: list[dict], target_package: str, dev: _Dev
) -> None:
    """5. target package installed."""
    try:
        package = str(target_package or "").strip()
        refusal = _package_refusal(package, dev.serial)
        if refusal is not None:
            checks.append(refusal)
            return
        checks.append(await _package_row(package, dev))
    except _STEP_ERRORS as exc:
        checks.append(_failed("package_installed", exc))
