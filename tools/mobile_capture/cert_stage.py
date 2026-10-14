"""Stage the certificate for a MANUAL user-store install on a non-rootable build."""

from __future__ import annotations

from tools.mobile import adb
from tools.mobile_capture import ca, cert_const
from tools.mobile_capture.cert_const import (
    REASON_DEVICE_UNREACHABLE,
    REASON_USER_STORE_MANUAL,
    USER_STORE_STAGE_DIR,
)
from tools.mobile_capture.cert_marker import _rc


async def _stage_push(serial: str, remote: str) -> dict | None:
    """The refusal when the push to Downloads fails, else None."""
    pushed = await adb.raw(
        ["-s", serial, "push", str(ca.cert_path()), remote],
        timeout=cert_const.DEVICE_CALL_TIMEOUT_S,
    )
    body = pushed.get("content") or {}
    if not (pushed.get("error") or _rc(body) != 0):
        return None
    return {
        "error": REASON_DEVICE_UNREACHABLE,
        "content": {
            "detail": (
                "could not stage the certificate on the device: "
                + str(pushed.get("error") or body.get("err") or "").strip()[:200]
            )
        },
    }


async def _stage_for_manual_install(serial: str, btype: str) -> dict:
    """Push the certificate to Downloads and hand over the manual steps.

    The device refuses ``adb root``, so the system store is unreachable and the
    only store left is the USER store -- which Android 11+ will not let adb
    write at all, by design. So this stages the file and stops. It reports
    :data:`REASON_USER_STORE_MANUAL`, never success: nothing is trusted until a
    human finishes it, and the tester must know the honest odds before they
    bother -- an app targeting API 24+ ignores the user store unless its own
    network-security config opts in, which almost none do.

    Never raises; a push failure is reported, not thrown.
    """
    remote = USER_STORE_STAGE_DIR + "/qa-agents-ca.crt"
    failed = await _stage_push(serial, remote)
    if failed:
        return failed
    return _stage_reply(btype, remote)


def _stage_reply(btype: str, remote: str) -> dict:
    """The honest not-a-success reply once the certificate is staged."""
    return {
        "error": REASON_USER_STORE_MANUAL,
        "content": {
            "build_type": btype or "unknown",
            "staged_at": remote,
            "installed": False,
            "trusted": False,
            "detail": _manual_install_detail(btype, remote),
        },
    }


def _manual_install_detail(btype: str, remote: str) -> str:
    """The honest manual-install steps and odds for a non-rootable build."""
    return (
        "This device reports a '"
        + (btype or "unknown")
        + "' build, which refuses `adb root`, so the SYSTEM "
        "certificate store cannot be reached. The certificate has "
        "been copied to "
        + remote
        + " instead. To finish, on the device: Settings -> Security "
        "-> Encryption & credentials -> Install a certificate -> CA "
        "certificate -> Install anyway, then pick qa-agents-ca.crt. "
        "Be warned this lands in the USER store, and an app built "
        "for Android 7 or later ignores that store unless the app "
        "itself opted in -- so for most apps this will change "
        "nothing, and HTTPS bodies will stay unreadable. Nothing is "
        "trusted until you complete those steps."
    )
