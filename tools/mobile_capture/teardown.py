"""The ``http_proxy == :0`` invariant. THE ONE place that decides "cleared".

Every teardown path in this lane ends here, directly or through :func:`reap`:
a normal stop, a failure mid-run, a crashed process, a disconnected device, the
next run's startup reap, and a second caller interleaved with the first on one
serial. See ``.claude/plans/plan-mobile-api-capture.md`` (Phase 3, T3.3) for
the six-path contract this module is graded against.

**THIS IS THE ONLY MODULE IN ``tools/mobile_capture`` THAT MAY TERMINATE A
PROCESS.** :func:`kill_process` is the one call site; every other module that
needs a proxy process gone calls it rather than reaching for ``os.kill`` or
``Popen.terminate`` itself. ``tests/mobile_capture/test_proxy_eviction.py``
sweeps the package with ``ast`` to keep that true.

**The ordering invariant this module is the second half of.** ``proxy.start``
writes the runtime marker (:func:`tools.mobile_capture.paths.proxy_marker`)
BEFORE it ever sets the device's ``http_proxy``. That is what makes a marker
whose ``http_proxy`` was never actually applied safe to ignore, and a marker
whose setting WAS applied always findable by :func:`reap` even after this
process dies -- there is no third state.

**Concurrency (T3.2a).** Every entry point here -- :func:`clear` included --
runs under the lane's EXISTING per-serial device lock,
``tools.mobile.session.take_device_lock``. A caller that already holds it for
this serial (``proxy.start``'s own ``run_id``) passes it as *owner* and the
take is a free re-entry; a caller with no lock of its own mints a throwaway
label, takes the lock, and releases it itself -- so a second, unrelated
caller racing the first is refused BY NAME rather than clearing a marker the
first is still using (T3.3's sixth exit path).

**A device that cannot be reached is not treated as clear.** :func:`clear`
kills the LOCAL ``mitmdump`` process unconditionally -- that is a host-side
act, independent of whether the device answers -- but it leaves the marker ON
DISK when the ``adb`` calls themselves fail, and reports ``dangling=True``
rather than guessing the device is safe. Because the local process is already
dead by then, that marker is picked up as ordinary crash residue the next time
:func:`reap` runs (at the top of every :func:`tools.mobile_capture.proxy.start`
call) -- so "the device came back" and "the process crashed" are the SAME
recovery path, not two.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import time
from pathlib import Path

from tools.mobile import adb, session
from tools.mobile_capture import paths

logger = logging.getLogger(__name__)

#: The TERM -> KILL wait, paid on every exit path including the failure ones.
#: See tests/test_bounds_upper.py::CEILINGS for the constraint that sizes it.
STOP_TIMEOUT_S: int = 10

#: Passed as ``timeout=`` to EACH of the three ``adb`` calls inside
#: :func:`clear` (the ``put``, the ``reverse_remove``, and the read-back) --
#: not a documented aspiration, an argument that actually reaches
#: ``asyncio.wait_for`` in ``tools/mobile/adb.py``. Worst case for one
#: :func:`clear` call is therefore bounded at ``3 * CLEAR_TIMEOUT_S``, never
#: adb's own general-purpose ``DEFAULT_TIMEOUT_S``.
#: See tests/test_bounds_upper.py::CEILINGS.
CLEAR_TIMEOUT_S: int = 10

HTTP_PROXY_SETTING = "http_proxy"
HTTP_PROXY_OFF = ":0"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # A process we cannot signal is still a process; treat it as alive
        # rather than assume it is gone and skip trying to stop it.
        return True
    except OSError:
        return False
    return True


def kill_process(pid: int) -> None:
    """TERM, then -- after :data:`STOP_TIMEOUT_S` -- KILL. Best effort, never raises.

    THE ONLY PLACE THIS PACKAGE TERMINATES A PROCESS. Every other module that
    needs a ``mitmdump`` gone calls this rather than signalling it directly, so
    "no call site terminates a proxy outside teardown.py"
    (``tests/mobile_capture/test_proxy_eviction.py``) is an actual invariant,
    not a convention nobody checks.
    """
    if pid <= 0:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    deadline = time.monotonic() + STOP_TIMEOUT_S
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def _read_marker(serial: str) -> dict | None:
    target = paths.proxy_marker(serial)
    try:
        if not target.is_file():
            return None
        body = json.loads(target.read_text(encoding="utf-8"))
        return body if isinstance(body, dict) else None
    except Exception:
        logger.warning("mobile_capture.teardown: unreadable marker for %s", serial)
        return None


def _delete_marker(serial: str) -> None:
    try:
        paths.proxy_marker(serial).unlink(missing_ok=True)
    except OSError:
        pass


def boot_id() -> str:
    """A string that changes across a host reboot, or ``""`` when unknowable.

    THE ONE PRODUCER of this fact: :mod:`tools.mobile_capture.proxy` stamps a
    marker with this at spawn time, and :func:`is_crash_residue` reads it back
    here rather than a second module deriving it a second way.
    """
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def is_crash_residue(body: dict) -> bool:
    """True when *body* names a proxy that cannot possibly still be running.

    Exactly T3.2b's rule: a dead pid, or a boot id that does not match this
    boot (a marker surviving a host restart, whose pid number may since have
    been recycled by an unrelated process -- a live-looking pid that is
    actually residue). Anything else is a genuinely live proxy and this
    function says so by returning ``False``; the caller must not touch it.
    """
    pid = int(body.get("pid") or 0)
    stamped_boot = str(body.get("boot_id") or "")
    this_boot = boot_id()
    if this_boot and stamped_boot and stamped_boot != this_boot:
        return True
    return not _pid_alive(pid)


async def clear(serial: str, *, owner: str = "", reason: str = "") -> dict:
    """Set ``http_proxy`` to ``:0``, remove the reverse, kill the marker's pid,
    delete the marker, and READ THE SETTING BACK. The result is the read-back,
    never the ``put`` call's exit code -- a ``put`` that silently no-ops must
    not be reported as a clear.

    **Runs under the per-serial device lock (T3.2a), like every other entry
    point into capture.** A caller that ALREADY holds it for this serial
    passes its own *owner* (``proxy.start``'s ``run_id``) and the take is a
    free re-entry; a caller with none mints a throwaway
    :func:`tools.mobile.session.new_provisioning_owner` label, takes the
    lock, and releases it itself when this call returns -- so a second
    caller (T3.3's sixth exit path) is refused BY NAME rather than clearing a
    marker the first is still using.

    ``{"cleared": bool, "read_back": str, "dangling": bool, "busy": bool}``.
    This is the function every setter in ``proxy.py`` is paired with (T3.3's
    authoring guard), and the one :func:`reap` calls on every marker it
    finds to be crash residue.
    """
    serial = str(serial)
    label = str(owner) if owner else session.new_provisioning_owner()
    took = session.take_device_lock(label, serial=serial)
    acquired = ((took or {}).get("content") or {}).get("acquired")
    if not acquired:
        holder = str(((took or {}).get("content") or {}).get("holder") or "")
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
    try:
        marker = _read_marker(serial) or {}
        pid = int(marker.get("pid") or 0)
        if pid:
            # Host-local, independent of whether the device can be reached
            # at all: a device that never answers again must not also leak
            # the process that was proxying it.
            kill_process(pid)

        put = await adb.global_setting_put(
            serial, HTTP_PROXY_SETTING, HTTP_PROXY_OFF, timeout=CLEAR_TIMEOUT_S
        )
        port = int(marker.get("port") or 0)
        if port:
            await adb.reverse_remove(
                serial, "tcp:" + str(port), timeout=CLEAR_TIMEOUT_S
            )
        read = await adb.global_setting_get(
            serial, HTTP_PROXY_SETTING, timeout=CLEAR_TIMEOUT_S
        )

        if put.get("error") or read.get("error"):
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
            return {
                "cleared": False,
                "read_back": "",
                "dangling": True,
                "busy": False,
            }

        read_back = str(read.get("content") or "")
        cleared = read_back == HTTP_PROXY_OFF
        if cleared:
            _delete_marker(serial)
        else:
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
        return {
            "cleared": cleared,
            "read_back": read_back,
            "dangling": not cleared,
            "busy": False,
        }
    finally:
        if not owner:
            # We minted the label ourselves; give it back. A caller that
            # passed its OWN owner keeps its lock -- releasing here would
            # take the run's lease out from under it.
            session.release_device_lock(label, as_holder=True)


async def reap(*, owner: str = "", serial_hint: str = "") -> dict:
    """Clear every marker that is crash residue. Called at the top of every
    :func:`tools.mobile_capture.proxy.start` -- "reap on START as well as on
    stop" (the design brief). Never touches a marker whose pid is alive under
    THIS boot: that is a live proxy, and the only other logic near it is the
    cap refusal in ``proxy.py``, which refuses by name and never kills
    either.

    *owner*/*serial_hint*: when the CALLER already holds the device lock for
    one particular serial (``proxy.start`` reaping before its own spawn), that
    serial's :func:`clear` call reuses the caller's *owner* label so the take
    is a free re-entry rather than a refusal against its own hold. Every
    OTHER serial's residue is cleared through :func:`clear`'s own throwaway
    owner, taken and released around that one call -- reaping crash residue
    on a device nobody is driving must never contend with, or borrow the
    identity of, a lock this caller holds on a DIFFERENT device.
    """
    root = paths.capture_root()
    results: list[dict] = []
    if root.is_dir():
        for entry in sorted(root.glob("proxy-*.json")):
            try:
                body = json.loads(entry.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(body, dict):
                continue
            serial = str(body.get("serial") or "")
            if not serial or not is_crash_residue(body):
                continue
            reuse_owner = owner if (owner and serial == str(serial_hint)) else ""
            outcome = await clear(serial, owner=reuse_owner, reason="reap")
            results.append({"serial": serial, **outcome})
    return {"error": None, "content": {"reaped": results}}
