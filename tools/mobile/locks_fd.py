"""The kernel lock itself: take, drop and probe an exclusive ``flock`` on an fd.

Split out of :mod:`tools.mobile.locks` (the facade). Callers reach these
through the module object (``locks_fd._lock_fd``) so a patch here bites.
"""

from __future__ import annotations

import errno
import logging
import os
from typing import Any, cast

from tools.mobile.locks_body import lock_path
from tools.mobile.locks_state import LockUnsupported

logger = logging.getLogger("tools.mobile.locks")

# Platform-conditional: fcntl is POSIX-only and msvcrt Windows-only, so each is None where absent.
try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = cast("Any", None)
try:
    import msvcrt
except ImportError:
    msvcrt = cast("Any", None)


# Non-blocking is not a preference: a mobile tool call that waited on the
# emulator would die at the client's own timeout and tell the tester nothing,
# where a refusal names the holder and offers a way forward.
def _posix_lock(fd: int) -> bool:
    """Take the POSIX lock; ``False`` when ``fcntl`` does not exist here."""
    if fcntl is None:  # pragma: no cover - Windows
        return False
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return True


def _windows_lock(fd: int) -> None:
    """Take the ``msvcrt`` lock, or raise :class:`LockUnsupported`."""
    if msvcrt is None:  # pragma: no cover - neither primitive exists
        raise LockUnsupported("neither fcntl nor msvcrt is available")
    # pragma: no cover - no Windows machine has run this
    #
    # The pad byte is written ONLY when the file is empty. `msvcrt.locking`
    # needs the byte region to exist, but writing unconditionally overwrites the
    # first byte of an INCUMBENT holder's JSON body on a failed acquire -- and
    # then `_read` cannot parse it and the refusal loses the holder's name,
    # which is the one thing the message exists to say.
    if os.fstat(fd).st_size == 0:
        os.write(fd, b" ")
    os.lseek(fd, 0, os.SEEK_SET)
    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]  # msvcrt.locking is win32-only in typeshed


# WINDOWS TAKES A DIFFERENT STEP ORDER. ``msvcrt.locking`` locks a BYTE REGION
# from the current file position and needs that region to exist, so locking
# byte 0 of a freshly created empty file is not the POSIX sequence with a
# different call name -- the caller writes the body first there. **No Windows
# machine has run this code**, consistent with the mobile lane's standing
# disclosure in CLAUDE.md and docs/MOBILE_TESTING.md.
def lock_fd(fd: int) -> None:
    """Take an exclusive, NON-BLOCKING lock on *fd*, or raise."""
    if _posix_lock(fd):
        return
    _windows_lock(fd)


def unlock_fd(fd: int) -> None:
    if fcntl is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            # The fd is closed right after this, which releases it anyway.
            logger.debug("mobile.locks: unlock call failed for fd %s", fd)
        return
    try:  # pragma: no cover - Windows only
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]  # msvcrt.locking is win32-only in typeshed
    except (AttributeError, OSError):  # pragma: no cover - Windows only
        logger.debug("mobile.locks: unlock call failed for fd %s", fd)


#: errnos a non-blocking exclusive lock reports when another holder has it
#: (``flock`` -> EWOULDBLOCK/EAGAIN, ``msvcrt.locking`` -> EACCES/EDEADLK).
_CONTENTION_ERRNOS = frozenset(
    {errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES, errno.EDEADLK}
)


def _is_contention(exc: OSError) -> bool:
    return isinstance(exc, BlockingIOError) or exc.errno in _CONTENTION_ERRNOS


def _close_quiet(fd: int) -> None:
    """Close *fd* when it is a real descriptor; never raise."""
    if fd >= 0:
        try:
            os.close(fd)
        except OSError:
            pass


# Takes the lock and drops it again, so the only lock it ever releases is its
# own -- the invariant is not bent. ``False`` on any error, because "unknown"
# must read as "busy": a diagnostic that guesses "free" would invite a caller
# to act on a device somebody else is driving.
#
# ITS COST, stated honestly rather than waved away. This holds the exclusive
# lock for the width of one ``flock`` pair, and for that instant a REAL
# contender is refused. That used to be excused with "nothing but diagnostics
# calls this function", which stopped being true when ``qa-doctor`` -- the
# first tool a tester runs on a new machine -- became a caller. Measured at
# 41,055 probes against 22,571 real acquires in one 4-second race: 78 spurious
# refusals, 0.35%. qa-doctor probes ONCE per run, so a tester meets this at
# one flock pair wide, and a refusal is a message rather than a lost run --
# but the number is here so nobody has to guess again.
def probe_free(name: str) -> bool:
    """True when NO process holds *name* right now. Diagnostics only."""
    fd = -1
    try:
        target = lock_path(name)  # raises on a name that is not one
        if not target.parent.is_dir():
            return True
        fd = os.open(target, os.O_CREAT | os.O_RDWR, 0o600)
        lock_fd(fd)
        unlock_fd(fd)
        return True
    except (OSError, ValueError):
        return False
    finally:
        _close_quiet(fd)


def _lock_fd(fd: int) -> None:
    """Private name kept for out-of-wave callers; delegates to :func:`lock_fd` at call time."""
    return lock_fd(fd)


def _unlock_fd(fd: int) -> None:
    """Private name kept for out-of-wave callers; delegates to :func:`unlock_fd` at call time."""
    return unlock_fd(fd)


def _probe_free(name: str) -> bool:
    """Private name kept for out-of-wave callers; delegates to :func:`probe_free` at call time."""
    return probe_free(name)
