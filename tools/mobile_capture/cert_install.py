"""The install ladder of the device root CA, behind the lock, ``apply`` and the flag."""

from __future__ import annotations

import logging
import time

from tools import settings_port
from tools.mobile import adb
from tools.mobile_capture.cert_const import (
    _ROOTABLE_BUILD_TYPES,
    REASON_DEVICE_UNREACHABLE,
    REASON_FLAG_OFF,
    REASON_NO_APPLY,
)
from tools.mobile_capture.cert_lock import _owner_label, _with_lock
from tools.mobile_capture.cert_marker import _write_marker
from tools.mobile_capture.cert_push import _push_pem
from tools.mobile_capture.cert_route import (
    _ca_identity,
    _pick_route,
    _root_device,
)
from tools.mobile_capture.cert_stage import _stage_for_manual_install

logger = logging.getLogger("tools.mobile_capture.cert")


def _unreachable(detail: str) -> dict:
    return {"error": REASON_DEVICE_UNREACHABLE, "content": {"detail": detail}}


async def _preflight(serial: str) -> tuple[dict | None, dict, str]:
    """(refusal, device facts, lower-cased ``ro.build.type``)."""
    facts = await adb.device_facts(serial)
    if facts.get("error"):
        return _unreachable(facts["error"]), facts, ""
    build_type = await adb.getprop(serial, "ro.build.type")
    if build_type.get("error"):
        return _unreachable(build_type["error"]), facts, ""
    return None, facts, str(build_type.get("content") or "").strip().lower()


async def _write_to_device(
    serial: str, facts: dict, leaf: str
) -> tuple[dict | None, str, str]:
    """(refusal, route, device path) after root, route choice and the push."""
    refusal = await _root_device(serial)
    if refusal:
        return refusal, "", ""
    refusal, device_dir, route = await _pick_route(serial, facts)
    if refusal:
        return refusal, "", ""
    refusal, device_path = await _push_pem(serial, device_dir, leaf)
    return refusal, route, device_path


def _marker_for(serial: str, found: tuple[str, str], ident: tuple[str, str]) -> dict:
    route, device_path = found
    fingerprint, leaf = ident
    return {
        "serial": serial,
        "route": route,
        "device_path": device_path,
        "fingerprint": fingerprint,
        "hash": leaf,
        "installed_ms": int(time.time() * 1000),
    }


async def _install_checked(serial: str) -> dict:
    """The install ladder once the lock, ``apply`` and the flag are settled."""
    refusal, fingerprint, leaf = _ca_identity()
    if refusal:
        return refusal
    refusal, facts, btype = await _preflight(serial)
    if refusal:
        return refusal
    if btype not in _ROOTABLE_BUILD_TYPES:
        return await _stage_for_manual_install(serial, btype)
    refusal, route, device_path = await _write_to_device(serial, facts, leaf)
    if refusal:
        return refusal
    marker = _marker_for(serial, (route, device_path), (fingerprint, leaf))
    _write_marker(serial, marker)
    return {"error": None, "content": marker}


async def _install_locked(serial: str, apply: bool) -> dict:
    if not apply:
        return {"error": REASON_NO_APPLY, "content": None}
    if not settings_port.get().qa_mobile_https_capture_enabled:
        return {"error": REASON_FLAG_OFF, "content": None}
    return await _install_checked(serial)


async def install(serial: str, *, apply: bool = False, owner: str = "") -> dict:
    """Install the qa-agents root CA into *serial*'s SYSTEM trust store.

    Refuses BY NAME, never a half-attempt: a production build, an unreadable
    API level, a failed remount and a failed push are each their own reason
    code, checked in that order, and nothing is written to the device past
    the first one that fails.
    """
    serial = str(serial or "")
    lock = _owner_label(owner)
    try:
        return await _with_lock(serial, lock, lambda: _install_locked(serial, apply))
    except Exception:
        logger.exception("mobile_capture.cert.install failed")
        return {"error": REASON_DEVICE_UNREACHABLE, "content": None}
