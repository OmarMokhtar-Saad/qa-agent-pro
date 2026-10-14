"""Spawn/stop the qa-agents-owned ``mitmdump``, and the device's ``http_proxy``.

**Concurrency (T3.2a).** Every entry point runs under the lane's EXISTING
per-serial device lock, ``tools.mobile.device_lock.take_device_lock`` -- no new
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

import logging
import os  # noqa: F401  (tests patch proxy.os.chmod)
import subprocess  # noqa: F401  (tests patch proxy.subprocess.Popen)

from tools.mobile import adb, device_lock  # noqa: F401  (tests patch these)
from tools.mobile_capture.proxy_proc import (  # noqa: F401
    ADDON_DIR_ENV,
    ADDON_PATH,
    ADDON_RUN_ID_ENV,
    HTTP_PROXY_SETTING,
    MAX_LIVE_PROXIES,
    REASON_DEVICE_BUSY,
    REASON_PROXY_START_FAILED,
    REASON_TOO_MANY_PROXIES,
    START_TIMEOUT_S,
    _cap_refusal,
    _free_port,
    live_markers,
    read_marker,
    spawn_proxy,
    wait_port_open,
    write_marker,
)
from tools.mobile_capture.proxy_start import (  # noqa: F401
    _start_apply,
    _start_locked,
    _start_spawn,
    start,
)

logger = logging.getLogger("tools.mobile_capture.proxy")
