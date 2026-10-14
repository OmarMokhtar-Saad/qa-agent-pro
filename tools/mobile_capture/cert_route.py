"""The device-side steps of a root-CA install: identity, root, route, push."""

from __future__ import annotations

from tools.mobile import adb
from tools.mobile_capture import ca, cert_const
from tools.mobile_capture.cert_const import (
    _HASH_NAME_RE,
    API_LEVEL_FOR_DATA_ROUTE,
    DATA_CACERTS_DIR,
    REASON_API_UNKNOWN,
    REASON_DEVICE_UNREACHABLE,
    REASON_NO_CA,
    REASON_PRODUCTION_BUILD,
    REASON_REMOUNT_FAILED,
    ROUTE_DATA,
    ROUTE_SYSTEM,
    SYSTEM_CACERTS_DIR,
)
from tools.mobile_capture.cert_marker import _rc


def _no_ca(detail: str) -> dict:
    return {"error": REASON_NO_CA, "content": {"detail": detail}}


def _hashed_leaf() -> tuple[dict | None, str]:
    """(refusal, android hash filename) of the root CA."""
    hashed = ca.android_hashed_name()
    if hashed.get("error"):
        return _no_ca(hashed["error"]), ""
    leaf = str(hashed["content"]["filename"])
    if not _HASH_NAME_RE.match(leaf):
        return _no_ca("unexpected hash filename shape"), ""
    return None, leaf


def _ca_identity() -> tuple[dict | None, str, str]:
    """(refusal, fingerprint, android hash filename) of the root CA."""
    made = ca.ensure_ca()
    if made.get("error"):
        return _no_ca(made["error"]), "", ""
    fingerprint = str((made.get("content") or {}).get("fingerprint") or "")

    refusal, leaf = _hashed_leaf()
    if refusal:
        return refusal, "", ""
    return None, fingerprint, leaf


def _root_refused(root_body: dict) -> dict:
    return {
        "error": REASON_PRODUCTION_BUILD,
        "content": {
            "detail": (
                "adb root was refused: "
                + str(root_body.get("err") or "").strip()[:200]
                + ". A Google Play (`google_apis_playstore`) "
                "image cannot root: create an AVD with a "
                "`google_apis` image in Android Studio's Device "
                "Manager to enable decryption."
            )
        },
    }


async def _root_device(serial: str) -> dict | None:
    """Run ``adb root``; the refusal when the device will not root, else None."""
    rooted = await adb.raw(
        ["-s", serial, "root"], timeout=cert_const.DEVICE_CALL_TIMEOUT_S
    )
    if rooted.get("error"):
        return {
            "error": REASON_DEVICE_UNREACHABLE,
            "content": {"detail": rooted["error"]},
        }
    root_body = rooted.get("content") or {}
    if _rc(root_body) != 0:
        return _root_refused(root_body)
    return None


def _api_level(facts: dict) -> int | None:
    api_raw = str((facts.get("content") or {}).get("api") or "")
    try:
        return int(api_raw)
    except (TypeError, ValueError, OverflowError):
        return None


def _api_unknown() -> dict:
    return {
        "error": REASON_API_UNKNOWN,
        "content": {"detail": "could not read ro.build.version.sdk"},
    }


def _remount_failed() -> dict:
    return {
        "error": REASON_REMOUNT_FAILED,
        "content": {
            "detail": (
                "`adb remount` failed -- this device's "
                "/system is not writable, most likely "
                "because it was not started with "
                "`-writable-system`. No certificate was "
                "installed."
            )
        },
    }


async def _remount_refusal(serial: str) -> dict | None:
    """Remount `/system` writable; the refusal when that fails, else None."""
    remounted = await adb.raw(
        ["-s", serial, "remount"], timeout=cert_const.DEVICE_CALL_TIMEOUT_S
    )
    if remounted.get("error"):
        return {
            "error": REASON_DEVICE_UNREACHABLE,
            "content": {"detail": remounted["error"]},
        }
    if _rc(remounted.get("content") or {}) != 0:
        return _remount_failed()
    return None


async def _pick_route(serial: str, facts: dict) -> tuple[dict | None, str, str]:
    """(refusal, device directory, route) chosen from the device API level."""
    api = _api_level(facts)
    if api is None:
        return _api_unknown(), "", ""

    if api >= API_LEVEL_FOR_DATA_ROUTE:
        return None, DATA_CACERTS_DIR, ROUTE_DATA
    refusal = await _remount_refusal(serial)
    if refusal:
        return refusal, "", ""
    return None, SYSTEM_CACERTS_DIR, ROUTE_SYSTEM
