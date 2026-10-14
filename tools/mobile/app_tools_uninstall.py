"""The uninstall half of ``app_tools``: recognise the signing-mismatch
question, word it for the tester, and run the one ``adb`` uninstall."""

from __future__ import annotations

from tools.mobile import adb, update_app
from tools.mobile.update_app import UpdateReport


def is_uninstall_question(report: UpdateReport) -> bool:
    prefix = update_app.CONFIRM_UNINSTALL + ":"
    return (
        report.status == update_app.STATUS_CONFIRM
        and report.confirm_action.startswith(prefix)
    )


def uninstall_prompt(report: UpdateReport) -> str:
    return (
        "Uninstall " + report.package + " from " + report.serial + " so the update "
        "can install? That deletes all of the app data on the device."
    )


async def uninstalled(report: UpdateReport) -> bool:
    """True only when adb removed the app. ``adb.uninstall`` reports ``error``
    None even on a non-zero exit, so the exit code is read here as well."""
    got = await adb.uninstall(report.serial, report.package)
    if got.get("error"):
        return False
    body = got.get("content")
    return not (isinstance(body, dict) and body.get("rc"))
