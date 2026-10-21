"""Is the server process that owned a run's lease still alive?

The lease records the owner's ``pid``. A stale lease whose pid is gone means the
server died (``server_died``); a stale lease whose pid still runs means the run
was left idle or superseded, which is not a death. No pid (an older lease) is
unknown and reads as not alive, the conservative old behaviour.
"""

from __future__ import annotations

import os

from tools import pid_probe


def owner_alive(lease: dict) -> bool:
    """True when the lease's recorded pid is a live process (this one included).

    The probe never signals the process (Windows reads its exit code instead of
    calling ``os.kill``). A reused pid also reads as alive.
    """
    try:
        pid = int(lease.get("pid") or 0)
    except (TypeError, ValueError, OverflowError):
        return False
    if pid <= 0:
        return False
    return pid == os.getpid() or pid_probe.pid_is_alive(pid)
