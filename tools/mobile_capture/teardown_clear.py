"""Clear a device's ``http_proxy`` under the per-serial lock and read it back."""

from __future__ import annotations

import logging
from typing import NamedTuple

from tools.mobile import adb, device_lock
from tools.mobile_capture import teardown_proc
from tools.mobile_capture.teardown_marker import (
    HTTP_PROXY_OFF,
    HTTP_PROXY_SETTING,
    _delete_marker,
    _read_marker,
)

logger = logging.getLogger("tools.mobile_capture.teardown")

#: Passed as ``timeout=`` to EACH of the three ``adb`` calls inside
#: :func:`clear` (the ``put``, the ``reverse_remove``, and the read-back) --
#: not a documented aspiration, an argument that actually reaches
#: ``asyncio.wait_for`` in ``tools/mobile/adb.py``. Worst case for one
#: :func:`clear` call is therefore bounded at ``3 * CLEAR_TIMEOUT_S``, never
#: adb's own general-purpose ``DEFAULT_TIMEOUT_S``.
#: See tests/test_bounds_upper.py::CEILINGS.
CLEAR_TIMEOUT_S: int = 10


class Readback(NamedTuple):
    """The two adb answers :func:`clear` judges: the ``put`` and the read-back."""

    put: dict
    read: dict


def _busy_result(serial: str, holder: str, reason: str) -> dict:
    logger.info(
        "mobile_capture.teardown: %s is locked by %s -- refusing to clear "
        "it from here (%s)",
        serial,
        holder,
        reason or "no reason given",
    )
    return {
        "cleared": False,
        "read_back": "",
        "dangling": True,
        "busy": True,
        "holder": holder,
    }


def _take(serial: str, owner: str) -> tuple[str, str | None]:
    """Take the device lock: ``(label, None)``, or ``(label, holder)`` if refused."""
    label = str(owner) if owner else device_lock.new_provisioning_owner()
    took = device_lock.take_device_lock(label, serial=serial)
    content = (took or {}).get("content") or {}
    if content.get("acquired"):
        return label, None
    return label, str(content.get("holder") or "")


async def _clear_locked(serial: str, reason: str) -> dict:
    marker = _read_marker(serial) or {}
    pid = int(marker.get("pid") or 0)
    if pid:
        # Host-local, independent of whether the device can be reached
        # at all: a device that never answers again must not also leak
        # the process that was proxying it.
        teardown_proc.kill_process(pid)
    readback = await _clear_device(serial, marker)
    return _clear_verdict(serial, reason, readback)


# Set ``http_proxy`` to ``:0``, remove the reverse, kill the marker's pid,
# delete the marker, and READ THE SETTING BACK. The result is the read-back,
# never the ``put`` call's exit code -- a ``put`` that silently no-ops must
# not be reported as a clear.
#
# **Runs under the per-serial device lock (T3.2a), like every other entry
# point into capture.** A caller that ALREADY holds it for this serial
# passes its own *owner* (``proxy.start``'s ``run_id``) and the take is a
# free re-entry; a caller with none mints a throwaway
# :func:`tools.mobile.device_lock.new_provisioning_owner` label, takes the
# lock, and releases it itself when this call returns -- so a second
# caller (T3.3's sixth exit path) is refused BY NAME rather than clearing a
# marker the first is still using.
#
# ``{"cleared": bool, "read_back": str, "dangling": bool, "busy": bool}``.
# This is the function every setter in ``proxy.py`` is paired with (T3.3's
# authoring guard), and the one :func:`reap` calls on every marker it
# finds to be crash residue.
async def clear(serial: str, *, owner: str = "", reason: str = "") -> dict:
    """Clear ``http_proxy`` to ``:0`` under the device lock; reply is the read-back."""
    serial = str(serial)
    label, holder = _take(serial, owner)
    if holder is not None:
        return _busy_result(serial, holder, reason)
    try:
        return await _clear_locked(serial, reason)
    finally:
        if not owner:
            # We minted the label ourselves; give it back. A caller that
            # passed its OWN owner keeps its lock -- releasing here would
            # take the run's lease out from under it.
            device_lock.release_device_lock(
                label, device_lock.DeviceRelease(as_holder=True)
            )


async def _clear_device(serial: str, marker: dict) -> Readback:
    """Put the proxy off, drop the reverse, read the setting back."""
    put = await adb.global_setting_put(
        serial,
        adb.SettingPut(HTTP_PROXY_SETTING, HTTP_PROXY_OFF, timeout=CLEAR_TIMEOUT_S),
    )
    port = int(marker.get("port") or 0)
    if port:
        await adb.reverse_remove(serial, "tcp:" + str(port), timeout=CLEAR_TIMEOUT_S)
    read = await adb.global_setting_get(
        serial, HTTP_PROXY_SETTING, timeout=CLEAR_TIMEOUT_S
    )
    return Readback(put, read)


def _unconfirmed_reply(serial: str, reason: str) -> dict:
    # The device could not be reached (or another adb failure). The
    # marker stays -- deleting it here would tell the next reap
    # there is nothing to check, which is exactly the "we guessed it
    # was clear" failure this function exists not to have.
    logger.warning(
        "mobile_capture.teardown: could not confirm %s cleared (%s) "
        "-- leaving its marker for the next reap",
        serial,
        reason or "no reason given",
    )
    return {"cleared": False, "read_back": "", "dangling": True, "busy": False}


def _still_proxied(serial: str, read_back: str) -> None:
    # adb answered on both calls, but the device itself still
    # reports a proxy set -- the write did not take (or something
    # else re-set it after). Reporting `cleared: True` here would be
    # the exact failure this module exists not to have: the caller
    # believes the device is safe while it is still routed at a
    # port whose process this call already killed. The marker stays
    # so the next reap can still find it, the same reason it stays
    # on an outright adb failure below.
    logger.warning(
        "mobile_capture.teardown: %s read back %r after clearing "
        "(expected %r) -- leaving its marker for the next reap",
        serial,
        read_back,
        HTTP_PROXY_OFF,
    )


def _readback_reply(serial: str, read: dict) -> dict:
    read_back = str(read.get("content") or "")
    cleared = read_back == HTTP_PROXY_OFF
    if cleared:
        _delete_marker(serial)
    else:
        _still_proxied(serial, read_back)
    return {
        "cleared": cleared,
        "read_back": read_back,
        "dangling": not cleared,
        "busy": False,
    }


def _clear_verdict(serial: str, reason: str, readback: Readback) -> dict:
    """Judge the read-back, keep or delete the marker, build the reply."""
    if readback.put.get("error") or readback.read.get("error"):
        return _unconfirmed_reply(serial, reason)
    return _readback_reply(serial, readback.read)
