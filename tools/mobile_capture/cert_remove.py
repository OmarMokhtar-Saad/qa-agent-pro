"""Status of, and removal of, the device root CA recorded by the local marker."""

from __future__ import annotations

import logging

from tools.mobile import adb
from tools.mobile_capture import cert_const, cert_marker, ledger
from tools.mobile_capture.cert_const import REASON_NO_APPLY, ROUTE_SYSTEM
from tools.mobile_capture.cert_lock import _owner_label, _with_lock
from tools.mobile_capture.cert_marker import _clear_marker, _rc

logger = logging.getLogger("tools.mobile_capture.cert")


def _status_body(marker: dict | None) -> dict:
    return {
        "installed": bool(marker),
        "route": str((marker or {}).get("route") or ""),
        "device_path": str((marker or {}).get("device_path") or ""),
        "fingerprint": str((marker or {}).get("fingerprint") or ""),
        "hash": str((marker or {}).get("hash") or ""),
        "installed_ms": (marker or {}).get("installed_ms"),
    }


def status(serial: str) -> dict:
    """The LOCAL record of what ``install()`` last did for *serial*.

    Reads only the on-disk marker -- no device is touched, so no ``apply``
    is needed, mirroring `mitm_provision.find_mitmdump`'s locate-first shape.
    """
    try:
        marker = cert_marker.read_cert_marker(str(serial or ""))
        return {"error": None, "content": _status_body(marker)}
    except Exception:
        logger.exception("mobile_capture.cert.status failed")
        return {"error": None, "content": _status_body(None)}


async def _make_writable(serial: str, route: str) -> bool:
    """Root + remount for a system-store route; other routes need neither."""
    if route != ROUTE_SYSTEM:
        return True
    timeout = cert_const.DEVICE_CALL_TIMEOUT_S
    rooted = await adb.raw(["-s", serial, "root"], timeout=timeout)
    if rooted.get("error") or _rc(rooted.get("content") or {}) != 0:
        return False
    remounted = await adb.raw(["-s", serial, "remount"], timeout=timeout)
    return not remounted.get("error") and _rc(remounted.get("content") or {}) == 0


async def _remove_from_device(serial: str, marker: dict) -> tuple[bool, str]:
    """``(device_removed, detail)`` after trying to ``rm`` the marked file."""
    device_path = str(marker.get("device_path") or "")
    route = str(marker.get("route") or "")
    reachable = await _make_writable(serial, route)
    if not (reachable and device_path):
        return False, "could not reach the device to remove the certificate"
    rm = await adb.shell(
        serial,
        ["rm", "-f", device_path],
        timeout=cert_const.DEVICE_CALL_TIMEOUT_S,
    )
    if not rm.get("error") and _rc(rm.get("content") or {}) == 0:
        return True, ""
    return False, str(rm.get("error") or (rm.get("content") or {}).get("err") or "")[
        :200
    ]


async def _remove_recorded(serial: str) -> dict:
    """Remove what the local marker records, then forget the device."""
    marker = cert_marker.read_cert_marker(serial)
    device_removed = False
    if marker:
        device_removed, detail = await _remove_from_device(serial, marker)
        _clear_marker(serial)
    else:
        detail = "no local record of an installed certificate on this device"

    ledger.forget(serial)
    return {
        "error": None,
        "content": {
            "device_removed": device_removed,
            "had_marker": bool(marker),
            "detail": detail,
        },
    }


async def _remove_locked(serial: str, apply: bool) -> dict:
    if not apply:
        return {"error": REASON_NO_APPLY, "content": None}
    return await _remove_recorded(serial)


def _remove_crashed(serial: str) -> dict:
    ledger.forget(serial)
    return {
        "error": None,
        "content": {
            "device_removed": False,
            "had_marker": False,
            "detail": "unexpected error while removing the certificate",
        },
    }


async def remove(serial: str, *, apply: bool = False, owner: str = "") -> dict:
    """Take the certificate off *serial* AND forget it in the ledger.

    ``ledger.forget`` is OUR OWN memory of trusting this device and is
    always called (barring the lock/apply refusals below) -- reported
    honestly against whether the on-device file could actually be
    confirmed removed, never conflated with it.
    """
    serial = str(serial or "")
    lock = _owner_label(owner)
    try:
        return await _with_lock(serial, lock, lambda: _remove_locked(serial, apply))
    except Exception:
        logger.exception("mobile_capture.cert.remove failed")
        return _remove_crashed(serial)
