"""Spawn/stop the qa-agents-owned ``mitmdump``, and the device's ``http_proxy``.

**Concurrency (T3.2a).** Every entry point runs under the lane's EXISTING
per-serial device lock, ``tools.mobile.session.take_device_lock`` -- no new
lock is invented, because a second serialisation primitive for the same
device is two answers to one question. Acquisition is idempotent for the same
owner in the same process (that function's own docstring), so the run path,
which already holds the lock under its own ``run_id``, passes that same id and
the take is free; a different owner is refused by name
(:data:`REASON_DEVICE_BUSY`), naming the holder, never a silent wait and never
a second proxy. :func:`tools.mobile_capture.teardown.clear` carries the same
guarantee on its own, independently, for callers that reach it directly.

**Eviction (T3.2b) never kills a live proxy.** Reaching :data:`MAX_LIVE_PROXIES`
refuses by name (:data:`REASON_TOO_MANY_PROXIES`); only crash residue (see
:func:`tools.mobile_capture.teardown.is_crash_residue`) is ever torn down, and
that goes through :func:`tools.mobile_capture.teardown.clear` in full -- the
same function a normal stop uses, never a bare kill. This module holds no
``os.kill`` or ``Popen.terminate`` call of its own; a failed spawn is cleaned
up through :func:`tools.mobile_capture.teardown.kill_process`.

**The ordering invariant.** The runtime marker
(:func:`tools.mobile_capture.paths.proxy_marker`) is written BEFORE the
device's ``http_proxy`` is ever set. A crash between the two can then never
leave a proxy nothing knows about: either the marker exists and the device was
never pointed at it, or both exist and the marker is what lets a later
:func:`tools.mobile_capture.teardown.reap` find and clear it. Every setter of a
non-``:0`` value below is inside a ``try/finally`` whose ``finally`` reaches
``teardown.clear`` on anything short of full success -- the authoring guard
``tests/mobile_capture/test_teardown_invariant.py`` pins by AST.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import time
from pathlib import Path

from tools.mobile import adb, session
from tools.mobile_capture import paths, teardown

logger = logging.getLogger(__name__)

#: How long ONE spawn is waited on before the run continues WITHOUT capture.
#: See tests/test_bounds_upper.py::CEILINGS.
START_TIMEOUT_S: int = 20

#: Concurrently PROXIED devices, one ``mitmdump`` each.
#: See tests/test_bounds_upper.py::CEILINGS.
MAX_LIVE_PROXIES: int = 2

REASON_DEVICE_BUSY = "device_busy"
REASON_TOO_MANY_PROXIES = "too_many_proxies"
REASON_PROXY_START_FAILED = "proxy_start_failed"

#: Loaded by ``mitmdump -s`` as its addon script. Phase 5 (``addon.py``) is
#: what actually writes flows there; this module only needs the path, fixed
#: now so that phase does not have to touch this constant.
ADDON_PATH: Path = Path(__file__).resolve().with_name("addon.py")

HTTP_PROXY_SETTING = "http_proxy"

#: The ONLY channel to the addon's OTHER interpreter (T5.1): it reads these
#: two names from its own environment, never anything imported from this
#: tree. Both are set on the ``mitmdump`` child's environment, never on this
#: process's own -- a leaked env var here would let a later, unrelated call
#: in THIS process pick up a stale run id.
# Off the QA_ prefix on purpose -- see the note beside addon.RUN_ID_ENV.
# These two literals must stay in step with the addon's, which the addon
# contract test asserts rather than trusting.
ADDON_RUN_ID_ENV = "MITMCAP_RUN_ID"
ADDON_DIR_ENV = "MITMCAP_DIR"


def _free_port() -> int:
    """An ephemeral localhost port, free at the instant this returns."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_port_open(port: int, timeout_s: float) -> bool:
    """Blocking poll for *port* to accept a connection. A thin, patchable seam."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            try:
                sock.connect(("127.0.0.1", port))
                return True
            except OSError:
                time.sleep(0.05)
    return False


def _spawn(port: int, ca_dir: Path) -> subprocess.Popen:
    """The ONE place ``mitmdump`` is spawned. ``Popen``, never ``shell=True``."""
    return subprocess.Popen(
        [
            "mitmdump",
            "-q",
            "--listen-host",
            "127.0.0.1",
            "--listen-port",
            str(port),
            "--set",
            "confdir=" + str(ca_dir),
            "-s",
            str(ADDON_PATH),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def read_marker(serial: str) -> dict | None:
    """The runtime marker for *serial*, or ``None``. Never raises."""
    target = paths.proxy_marker(serial)
    try:
        if not target.is_file():
            return None
        body = json.loads(target.read_text(encoding="utf-8"))
        return body if isinstance(body, dict) else None
    except Exception:
        logger.warning("mobile_capture.proxy: unreadable marker for %s", serial)
        return None


def write_marker(serial: str, body: dict) -> None:
    """Write the marker atomically. THE ONE PRODUCER of the marker's shape."""
    target = paths.proxy_marker(serial)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(body, sort_keys=True), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, target)


def live_markers(*, exclude_serial: str = "") -> list[tuple[str, dict]]:
    """``(serial, marker)`` for every marker that is a genuinely LIVE proxy.

    Excludes crash residue (:func:`tools.mobile_capture.teardown.is_crash_residue`
    -- the ONE producer of that judgement, not re-derived here) and, optionally,
    one serial: the cap check below uses this to keep a caller's OWN slot from
    ever counting against itself.
    """
    out: list[tuple[str, dict]] = []
    root = paths.capture_root()
    if not root.is_dir():
        return out
    for entry in sorted(root.glob("proxy-*.json")):
        try:
            body = json.loads(entry.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(body, dict):
            continue
        serial = str(body.get("serial") or "")
        if not serial or serial == str(exclude_serial):
            continue
        if not teardown.is_crash_residue(body):
            out.append((serial, body))
    return out


def _cap_refusal(serial: str) -> dict | None:
    """``None`` when there is room; a refusal naming every holder otherwise.

    NEVER KILLS. Reaching the cap refuses by name (T3.2b); the only path that
    ever tears a live proxy down is crash-residue reaping in ``teardown.py``,
    never this function.
    """
    live = live_markers(exclude_serial=serial)
    if len(live) < MAX_LIVE_PROXIES:
        return None
    holders = [{"serial": s, "run_id": str(b.get("run_id") or "")} for s, b in live]
    return {
        "error": REASON_TOO_MANY_PROXIES,
        "content": {"max": MAX_LIVE_PROXIES, "holders": holders},
    }


async def start(serial: str, *, run_id: str) -> dict:
    """Spawn ``mitmdump`` for *serial* and point it at the device. Never raises.

    Runs under the per-serial device lock (T3.2a): the same *run_id* re-enters
    for free; a different owner already holding it is refused by name and
    spawns nothing. Reaps crash residue first ("reap on start"), then checks
    :data:`MAX_LIVE_PROXIES`, then spawns.

    THE ORDERING INVARIANT: the marker is written before ``http_proxy`` is
    ever set, and the ``try/finally`` below undoes a partial start through
    :func:`tools.mobile_capture.teardown.clear` on anything short of full
    success.

    THE LOCK INVARIANT: a lock this call NEWLY acquired (as opposed to a
    re-entrant take on a lock the caller already held) is released on every
    path that returns WITHOUT a live proxy -- never on success, where
    ownership transfers to the caller's own lifecycle. After a failed
    ``start``, the device is therefore neither proxied nor locked.
    """
    try:
        serial = str(serial)
        took = session.take_device_lock(str(run_id), serial=serial)
        content = (took or {}).get("content") or {}
        if not content.get("acquired"):
            return {
                "error": REASON_DEVICE_BUSY,
                "content": {"holder": str(content.get("holder") or "")},
            }
        # NEWLY acquired vs RE-ENTERED (T3.2a's idempotent-same-owner path,
        # reported as `reentrant` by `take_device_lock`). A caller who already
        # held this lock before calling us keeps managing its own lifecycle --
        # this function must never give away a lock it did not take. A caller
        # for whom THIS call is what acquired the lock is the one who must
        # get it back on every path that does not hand off a live proxy: a
        # function that takes a resource, fails, and returns without giving
        # it back is the exact leak this phase exists to prevent, applied to
        # the lock instead of the proxy setting.
        newly_acquired = not content.get("reentrant")
        succeeded = False
        try:
            await teardown.reap(owner=str(run_id), serial_hint=serial)

            capped = _cap_refusal(serial)
            if capped is not None:
                return capped

            port = _free_port()
            # *run_id* and the capture tmp directory reach the addon ONLY
            # through the CHILD's environment (T5.1): they cannot be new
            # arguments on `_spawn` itself without breaking every existing
            # fake that patches `_spawn` with its ORIGINAL two-argument
            # shape (tests/mobile_capture/test_proxy_start.py and
            # test_proxy_uncovered_branches.py both do). `_spawn` still
            # reads `os.environ` un-touched (its own `env=None` default),
            # so the mutation below is scoped to the single synchronous
            # statement that calls it -- no `await` sits between the two,
            # so no other task in this process can observe it, and the
            # `finally` restores the parent's environment unconditionally.
            _prev_env = dict(os.environ)
            try:
                os.environ[ADDON_RUN_ID_ENV] = paths.safe_name(run_id)
                os.environ[ADDON_DIR_ENV] = str(paths.tmp_dir())
                proc = _spawn(port, paths.ca_dir())
            finally:
                os.environ.clear()
                os.environ.update(_prev_env)
            if not _wait_port_open(port, START_TIMEOUT_S):
                # The leaked-process class: a spawn that never binds must not
                # be left running, and must never have the proxy setting
                # applied.
                teardown.kill_process(proc.pid)
                return {"error": REASON_PROXY_START_FAILED, "content": None}

            proxy_set = False
            try:
                write_marker(
                    serial,
                    {
                        "pid": proc.pid,
                        "port": port,
                        "run_id": str(run_id),
                        "serial": serial,
                        "started_ms": int(time.time() * 1000),
                        "boot_id": teardown.boot_id(),
                    },
                )
                rev = await adb.reverse(serial, "tcp:" + str(port), "tcp:" + str(port))
                if rev.get("error"):
                    return {"error": rev["error"], "content": None}
                put = await adb.global_setting_put(
                    serial, HTTP_PROXY_SETTING, "127.0.0.1:" + str(port)
                )
                if put.get("error"):
                    return {"error": put["error"], "content": None}
                read = await adb.global_setting_get(serial, HTTP_PROXY_SETTING)
                if read.get("error"):
                    return {"error": read["error"], "content": None}
                proxy_set = True
                succeeded = True
                return {
                    "error": None,
                    "content": {
                        "port": port,
                        "pid": proc.pid,
                        "http_proxy": str(read.get("content") or ""),
                    },
                }
            finally:
                if not proxy_set:
                    await teardown.clear(
                        serial, owner=str(run_id), reason="start_failed"
                    )
        finally:
            # The LOCK invariant, mirrored from the proxy-setting invariant
            # above. A caller who did not hold this lock before calling us
            # does not keep holding it after a failure; a caller who already
            # held it keeps its own lifecycle unaffected, since this call
            # never released anything it did not itself acquire.
            if newly_acquired and not succeeded:
                session.release_device_lock(str(run_id), as_holder=True)
    except Exception as exc:
        logger.exception("mobile_capture.proxy.start failed")
        return {"error": str(exc), "content": None}
