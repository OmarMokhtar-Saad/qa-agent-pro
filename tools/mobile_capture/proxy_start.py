"""``start``: spawn ``mitmdump`` for a serial and point the device at it.

Leaf of ``tools.mobile_capture.proxy`` (the facade; the module docstring there
holds the concurrency, eviction and ordering rationale). Never raises.

Runs under the per-serial device lock (T3.2a): the same *run_id* re-enters
for free; a different owner already holding it is refused by name and
spawns nothing. Reaps crash residue first ("reap on start"), then checks
:data:`MAX_LIVE_PROXIES`, then spawns.

THE ORDERING INVARIANT: the marker is written before ``http_proxy`` is
ever set, and the ``try/finally`` in ``_start_apply`` undoes a partial start
through :func:`tools.mobile_capture.teardown.clear` on anything short of full
success.

THE LOCK INVARIANT: a lock this call NEWLY acquired (as opposed to a
re-entrant take on a lock the caller already held) is released on every
path that returns WITHOUT a live proxy -- never on success, where
ownership transfers to the caller's own lifecycle. After a failed
``start``, the device is therefore neither proxied nor locked.
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import NamedTuple

from tools.mobile import adb, device_lock
from tools.mobile_capture import paths, proxy_proc, teardown
from tools.process_env import restore_env, set_env, snapshot_env

logger = logging.getLogger("tools.mobile_capture.proxy")


class Spawned(NamedTuple):
    """A started ``mitmdump``: the port it listens on and its process."""

    port: int
    proc: subprocess.Popen


# NEWLY acquired vs RE-ENTERED (T3.2a's idempotent-same-owner path,
# reported as `reentrant` by `take_device_lock`). A caller who already
# held this lock before calling us keeps managing its own lifecycle --
# this function must never give away a lock it did not take. A caller
# for whom THIS call is what acquired the lock is the one who must
# get it back on every path that does not hand off a live proxy: a
# function that takes a resource, fails, and returns without giving
# it back is the exact leak this phase exists to prevent, applied to
# the lock instead of the proxy setting.
def _take(serial: str, run_id: str) -> tuple[dict | None, bool]:
    """``(refusal, newly_acquired)``: refusal is set when the lock is held."""
    took = device_lock.take_device_lock(str(run_id), serial=serial)
    content = (took or {}).get("content") or {}
    if not content.get("acquired"):
        holder = {"holder": str(content.get("holder") or "")}
        return {"error": proxy_proc.REASON_DEVICE_BUSY, "content": holder}, False
    return None, not content.get("reentrant")


async def _run_locked(serial: str, run_id: str, newly_acquired: bool) -> dict:
    """Start under the held lock; release a NEWLY taken lock unless a proxy lives."""
    reply = {"error": proxy_proc.REASON_PROXY_START_FAILED, "content": None}
    try:
        reply = await _start_locked(serial, run_id)
        return reply
    finally:
        # The LOCK invariant: release only a lock this call took, and only
        # when no live proxy is handed off (also on an exception).
        if newly_acquired and reply["error"] is not None:
            device_lock.release_device_lock(
                str(run_id), device_lock.DeviceRelease(as_holder=True)
            )


# start: the marker is written before ``http_proxy`` is ever set, and the
# ``try/finally`` below undoes a partial start through
# :func:`tools.mobile_capture.teardown.clear` on anything short of full success.
async def start(serial: str, *, run_id: str) -> dict:
    """Spawn ``mitmdump`` for *serial* and point it at the device. Never raises."""
    try:
        serial = str(serial)
        refusal, newly_acquired = _take(serial, run_id)
        if refusal is not None:
            return refusal
        return await _run_locked(serial, run_id, newly_acquired)
    except Exception as exc:
        logger.exception("mobile_capture.proxy.start failed")
        return {"error": str(exc), "content": None}


async def _start_locked(serial: str, run_id: str) -> dict:
    """Reap residue, check the cap, spawn and apply, with the device lock held."""
    await teardown.reap(owner=str(run_id), serial_hint=serial)

    capped = proxy_proc.cap_refusal(serial)
    if capped is not None:
        return capped

    port = proxy_proc._free_port()
    proc = _start_spawn(run_id, port)
    if not proxy_proc.wait_port_open(port, proxy_proc.START_TIMEOUT_S):
        # The leaked-process class: a spawn that never binds must not be left
        # running, and must never have the proxy setting applied.
        teardown.kill_process(proc.pid)
        return {"error": proxy_proc.REASON_PROXY_START_FAILED, "content": None}

    return await _start_apply(serial, run_id, Spawned(port, proc))


# *run_id* and the capture tmp directory reach the addon ONLY
# through the CHILD's environment (T5.1): they cannot be new
# arguments on `spawn_proxy` itself without breaking every existing
# fake that patches `spawn_proxy` with its ORIGINAL two-argument
# shape (tests/mobile_capture/test_proxy_start.py and
# test_proxy_uncovered_branches.py both do). `spawn_proxy` still
# reads `os.environ` un-touched (its own `env=None` default),
# so the mutation below is scoped to the single synchronous
# statement that calls it -- no `await` sits between the two,
# so no other task in this process can observe it, and the
# `finally` restores the parent's environment unconditionally.
def _start_spawn(run_id: str, port: int):
    """Spawn ``mitmdump`` with the addon's env set only around the spawn call."""
    _prev_env = snapshot_env()
    try:
        set_env(proxy_proc.ADDON_RUN_ID_ENV, paths.safe_name(run_id))
        set_env(proxy_proc.ADDON_DIR_ENV, str(paths.tmp_dir()))
        proc = proxy_proc.spawn_proxy(port, paths.ca_dir())
    finally:
        restore_env(_prev_env)
    return proc


def _error_reply(reply: dict) -> dict:
    """The ``{"error", "content": None}`` envelope for a failed adb *reply*."""
    return {"error": reply["error"], "content": None}


async def _prepare_device(serial: str, run_id: str, spawned: Spawned) -> dict | None:
    """Write the marker, then reverse the port; an error reply, or ``None``."""
    port = str(spawned.port)
    proxy_proc.write_marker(
        serial,
        {
            "pid": spawned.proc.pid,
            "port": spawned.port,
            "run_id": str(run_id),
            "serial": serial,
            "started_ms": int(time.time() * 1000),
            "boot_id": teardown.boot_id(),
        },
    )
    rev = await adb.reverse(serial, adb.PortSpec("tcp:" + port, "tcp:" + port))
    return _error_reply(rev) if rev.get("error") else None


async def _read_back(serial: str, spawned: Spawned) -> dict:
    """Read ``http_proxy`` back; the success reply, or an error reply."""
    read = await adb.global_setting_get(serial, proxy_proc.HTTP_PROXY_SETTING)
    if read.get("error"):
        return _error_reply(read)
    content = {
        "port": spawned.port,
        "pid": spawned.proc.pid,
        "http_proxy": str(read.get("content") or ""),
    }
    return {"error": None, "content": content}


async def _start_apply(serial: str, run_id: str, spawned: Spawned) -> dict:
    """Write the marker, point the device at the proxy, undo it on any failure."""
    proxy_set = False
    try:
        failed = await _prepare_device(serial, run_id, spawned)
        if failed:
            return failed
        target = "127.0.0.1:" + str(spawned.port)
        put = await adb.global_setting_put(
            serial, adb.SettingPut(proxy_proc.HTTP_PROXY_SETTING, target)
        )
        if put.get("error"):
            return _error_reply(put)
        reply = await _read_back(serial, spawned)
        proxy_set = reply["error"] is None
        return reply
    finally:
        if not proxy_set:
            await teardown.clear(serial, owner=str(run_id), reason="start_failed")
