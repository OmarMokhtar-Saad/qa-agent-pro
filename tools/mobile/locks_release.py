"""``release``: let go of a lock THIS PROCESS holds.

Split out of :mod:`tools.mobile.locks` (the facade). The fd is dropped through
``locks_fd._unlock_fd`` (module object) so a patch there bites.
"""

from __future__ import annotations

import logging
import os

from tools.mobile import locks_fd
from tools.mobile.locks_state import (
    _HELD,
    _MUTEX,
    EMULATOR_LOCK,
    Release,
    _Held,
    _refuse_name,
)

logger = logging.getLogger("tools.mobile.locks")


# RIGHT RUN, WRONG LEASE. A heartbeat writer from a chat that
# was displaced beats on until it notices, and when it does it
# must not give away the device the CURRENT holder of the same
# run is driving. A caller that presents no lease is a handler
# that has just claimed the run, and is trusted.
#
# NOTE THE MISSING `and mine.lease`. With it, a lock recorded
# with no lease was releasable by ANY writer for that run id --
# unreachable today only because every takeover route happens to
# adopt a lease, which is exactly the "unreachable by
# construction" argument that already failed twice here (the
# reaper, and the per-process placeholder). Without it, a writer
# can only release a lock whose lease authority was actually
# recorded, so recording it is load-bearing rather than
# decorative.
#
# The reason distinguishes the two cases, because a log
# line a future debugger reads should not say "a newer
# lease" when in fact NO lease was ever recorded.
def _lease_refusal(mine: _Held, req: Release) -> str | None:
    """Why the lease the caller presents does not fit, or ``None``."""
    if req.as_holder or str(req.lease or "") == mine.lease:
        return None
    if mine.lease:
        return "held under a different lease for " + mine.owner
    return "held with no recorded lease authority for " + mine.owner


def _release_refusal(mine: _Held, req: Release) -> str | None:
    """Why *mine* may not be released by this caller, or ``None`` when it may."""
    if req.force:
        return None
    if mine.owner != str(req.owner or "").strip():
        return "held by " + mine.owner
    return _lease_refusal(mine, req)


def _drop_held(key: str, mine: _Held) -> None:
    """Unlock and close the fd, and ALWAYS forget the entry."""
    try:
        locks_fd.unlock_fd(mine.fd)
        os.close(mine.fd)
    except OSError:
        # An fd already closed underneath us is still released. Popping
        # it is the never-raise contract AND the fd-leak guard: the
        # entry must go on EVERY path, including this one.
        logger.info("mobile.locks: fd for %s was already gone", key)
    finally:
        _HELD.pop(key, None)


def _reply(released: bool, reason: str) -> dict:
    """The ``release`` envelope."""
    return {"error": None, "content": {"released": released, "reason": reason}}


def _release_checked(name: str, req: Release) -> dict:
    """``release`` without the never-raise wrapper."""
    refusal = _refuse_name(name)
    if refusal is not None:
        return refusal
    key = str(name)
    with _MUTEX:
        mine = _HELD.get(key)
        if mine is None:
            return _reply(False, "not held")
        reason = _release_refusal(mine, req)
        if reason is not None:
            return _reply(False, reason)
        _drop_held(key, mine)
        # NO unlink. The file is immortal -- see the module docstring.
        return _reply(True, "")


# ``force`` bypasses only the owner LABEL, and only for a lock this process
# already holds. It can never touch another process's lock, because there is
# no code here that could: the fd is the ownership, and we do not have theirs.
#
# ``as_holder`` is a HANDLER saying "I have just claimed this run's lease, so
# I am its authority" -- and it has to be said, not inferred. It is opt-in at
# every seam above this one for that reason: while `session.release_device_lock`
# defaulted it to True, the DISPLACED branches -- which have just been told
# they lost the lease -- asserted it by saying nothing at all. The lease check
# used to skip itself for an empty ``lease``, which made the ABSENCE of a
# credential act as a credential: a writer that had lost its token was
# indistinguishable from a handler that had just claimed the run, and only
# ``heartbeat.start``'s refusal of an empty token kept it unreachable. That is
# the "unreachable by construction" argument this module's docstring already
# records failing twice.
def release(name: str = EMULATOR_LOCK, req: Release | None = None) -> dict:
    """Release a lock THIS PROCESS holds. ``{released, reason}``."""
    try:
        return _release_checked(name, req or Release())
    except Exception as exc:
        logger.exception("mobile.locks.release failed")
        return {"error": str(exc), "content": None}
