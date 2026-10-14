"""The one place this package terminates a process (``os.kill``)."""

from __future__ import annotations

import os
import signal
import time

#: The TERM -> KILL wait, paid on every exit path including the failure ones.
#: See tests/test_bounds_upper.py::CEILINGS for the constraint that sizes it.
STOP_TIMEOUT_S: int = 10


def pid_alive(pid: int) -> bool:
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


def _send_term(pid: int) -> bool:
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    return True


def _wait_dead(pid: int) -> bool:
    deadline = time.monotonic() + STOP_TIMEOUT_S
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.05)
    return False


def _send_kill(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


# TERM, then -- after :data:`STOP_TIMEOUT_S` -- KILL. Best effort, never raises.
#
# THE ONLY PLACE THIS PACKAGE TERMINATES A PROCESS. Every other module that
# needs a ``mitmdump`` gone calls this rather than signalling it directly, so
# "no call site terminates a proxy outside teardown.py"
# (``tests/mobile_capture/test_proxy_eviction.py``) is an actual invariant,
# not a convention nobody checks.
def kill_process(pid: int) -> None:
    """TERM, then KILL after STOP_TIMEOUT_S. Best effort, never raises."""
    if pid <= 0:
        return
    if not _send_term(pid):
        return
    if _wait_dead(pid):
        return
    _send_kill(pid)
