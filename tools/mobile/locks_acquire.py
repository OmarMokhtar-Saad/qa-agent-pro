"""``acquire``: take a lock, or refuse naming who holds it.

Split out of :mod:`tools.mobile.locks` (the facade). Calls the kernel lock and
the lock body through their module objects so a patch there bites.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import replace
from typing import NamedTuple

from tools.mobile import locks_body, locks_fd, paths
from tools.mobile.locks_holder import HELD_TOO_LONG_S
from tools.mobile.locks_state import (
    _HELD,
    _MUTEX,
    EMULATOR_LOCK,
    Claim,
    LockUnsupported,
    _Held,
    _refuse_name,
)

logger = logging.getLogger("tools.mobile.locks")

_ACQUIRED_REASONS = frozenset({"already_held", "acquired"})

_NO_OWNER = (
    "A lock needs an owner: an unlabelled holder cannot be "
    "named in a refusal and cannot be matched on release."
)


class Who(NamedTuple):
    """The ``owner`` and ``holder`` labels an ``acquire`` answer names."""

    label: str
    holder: str


def _acquire_result(reason: str, who: Who, *, same_process: bool = False) -> dict:
    """The ``acquire`` envelope. ``acquired`` and ``reentrant`` follow from *reason*."""
    return {
        "error": None,
        "content": {
            "acquired": reason in _ACQUIRED_REASONS,
            "owner": who.label,
            "holder": who.holder,
            "reentrant": reason == "already_held",
            "same_process": same_process,
            "reason": reason,
        },
    }


def _contended_result(key: str, label: str, moment: float) -> dict:
    """Refusal after the kernel said another process holds *key*; disclose a wedge."""
    body = locks_body.read(key) or {}
    age = locks_body._body_age(body, moment)
    if age > HELD_TOO_LONG_S:
        logger.critical(
            "mobile.locks: %s has been held by pid %s for %.0fs. "
            "Nothing here will break it -- a lock broken under a "
            "live holder is two holders. If that process is wedged, "
            "a human has to look.",
            key,
            body.get("pid"),
            age,
        )
    who = Who(label, str(body.get("owner") or ""))
    return _acquire_result("held_by_another_process", who)


def _acquire_in_process(mine: _Held, label: str, lease: str) -> dict:
    """The answer when this process already holds the lock."""
    if mine.owner == label:
        # ADOPT the caller's lease. A same-owner acquire means this
        # caller has just proved, through `session.claim`, that it
        # holds the run's lease -- so it becomes the authority for
        # releasing this lock, and the previous chat's writer stops
        # being able to. That is the whole point of recording it.
        if lease:
            mine.lease = str(lease)
        # Reentrant: no syscall, no write. This is what makes the
        # submit path free once the packet path has taken the lock.
        return _acquire_result("already_held", Who(label, label), same_process=True)
    who = Who(label, mine.owner)
    return _acquire_result("held_in_this_process", who, same_process=True)


def _lock_failure(fd: int, key: str, claim: Claim) -> dict | None:
    """The refusal for a failed kernel lock on *fd*, or ``None`` when it was taken."""
    try:
        locks_fd.lock_fd(fd)
    except LockUnsupported:
        os.close(fd)
        logger.warning(
            "mobile.locks: no file-locking facility on %s; refusing the "
            "lock rather than admitting two holders",
            os.name,
        )
        return _acquire_result("no_lock_facility", Who(claim.owner, ""))
    except OSError as exc:
        os.close(fd)
        if not locks_fd._is_contention(exc):
            # ENOLCK/EBADF/EIO: lock facility broken, not contention; let
            # `acquire` log it and answer with an error, not a busy refusal.
            raise
        return _contended_result(key, claim.owner, float(claim.now or 0.0))
    return None


def _acquire_file(key: str, claim: Claim, moment: float) -> dict:
    """Take the kernel lock on *key*'s file; caller holds ``_MUTEX``."""
    paths.ensure_tree()
    target = locks_body.lock_path(key)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_CREAT | os.O_RDWR, 0o600)
    failure = _lock_failure(fd, key, replace(claim, now=moment))
    if failure is not None:
        return failure
    lease = str(claim.lease or "")
    _HELD[key] = _Held(fd=fd, owner=claim.owner, since=moment, lease=lease)
    locks_body._stamp(key, fd, locks_body.Stamp(claim.owner, moment))
    return _acquire_result("acquired", Who(claim.owner, claim.owner))


def _acquire_checked(name: str, claim: Claim) -> dict:
    """``acquire`` without the never-raise wrapper."""
    refusal = _refuse_name(name)
    if refusal is not None:
        return refusal
    label = str(claim.owner or "").strip()
    if not label:
        return {"error": _NO_OWNER, "content": None}
    key = str(name)
    moment = float(claim.now) if claim.now is not None else time.time()
    with _MUTEX:
        mine = _HELD.get(key)
        if mine is not None:
            return _acquire_in_process(mine, label, claim.lease)
        return _acquire_file(key, replace(claim, owner=label), moment)


# ``{"error", "content": {"acquired", "owner", "holder", "reentrant",
# "same_process", "reason"}}``. The refusal is a CONTENT value rather than an
# error: "somebody else is using the emulator" is a normal state a handler
# renders, not a fault.
def acquire(name: str = EMULATOR_LOCK, claim: Claim | None = None) -> dict:
    """Take the lock, or refuse naming who holds it."""
    try:
        return _acquire_checked(name, claim or Claim())
    except Exception as exc:
        logger.exception("mobile.locks.acquire failed")
        return {"error": str(exc), "content": None}
