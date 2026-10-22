"""The ``http_proxy == :0`` invariant. THE ONE place that decides "cleared".

Every teardown path in this lane ends here, directly or through :func:`reap`:
a normal stop, a failure mid-run, a crashed process, a disconnected device, the
next run's startup reap, and a second caller interleaved with the first on one
serial. See the mobile API capture plan (Phase 3, T3.3) for
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
``tools.mobile.device_lock.take_device_lock``. A caller that already holds it for
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
import os  # noqa: F401  (tests patch teardown.os.kill: the shared os module)
from collections.abc import Iterator
from pathlib import Path

from tools.mobile import adb  # noqa: F401  (tests patch teardown.adb.*)
from tools.mobile_capture import paths
from tools.mobile_capture.teardown_clear import (  # noqa: F401
    CLEAR_TIMEOUT_S,
    _busy_result,
    _clear_device,
    _clear_verdict,
    clear,
)
from tools.mobile_capture.teardown_marker import (  # noqa: F401
    HTTP_PROXY_OFF,
    HTTP_PROXY_SETTING,
    _delete_marker,
    _read_marker,
    boot_id,
)
from tools.mobile_capture.teardown_proc import (  # noqa: F401
    STOP_TIMEOUT_S,
    kill_process,
    pid_alive,
)

logger = logging.getLogger("tools.mobile_capture.teardown")


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
    return not pid_alive(pid)


def _residue_bodies(root: Path) -> Iterator[dict]:
    """Every readable ``proxy-*.json`` marker under *root* that is a dict."""
    if not root.is_dir():
        return
    for entry in sorted(root.glob("proxy-*.json")):
        try:
            body = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            continue
        if isinstance(body, dict):
            yield body


# Clear every marker that is crash residue. Called at the top of every
# :func:`tools.mobile_capture.proxy.start` -- "reap on START as well as on
# stop" (the design brief). Never touches a marker whose pid is alive under
# THIS boot: that is a live proxy, and the only other logic near it is the
# cap refusal in ``proxy.py``, which refuses by name and never kills
# either.
#
# *owner*/*serial_hint*: when the CALLER already holds the device lock for
# one particular serial (``proxy.start`` reaping before its own spawn), that
# serial's :func:`clear` call reuses the caller's *owner* label so the take
# is a free re-entry rather than a refusal against its own hold. Every
# OTHER serial's residue is cleared through :func:`clear`'s own throwaway
# owner, taken and released around that one call -- reaping crash residue
# on a device nobody is driving must never contend with, or borrow the
# identity of, a lock this caller holds on a DIFFERENT device.
async def reap(*, owner: str = "", serial_hint: str = "") -> dict:
    """Clear every marker that is crash residue (called at the top of proxy start)."""
    results: list[dict] = []
    for body in _residue_bodies(paths.capture_root()):
        serial = str(body.get("serial") or "")
        if not serial or not is_crash_residue(body):
            continue
        reuse_owner = owner if (owner and serial == str(serial_hint)) else ""
        outcome = await clear(serial, owner=reuse_owner, reason="reap")
        results.append({"serial": serial, **outcome})
    return {"error": None, "content": {"reaped": results}}
