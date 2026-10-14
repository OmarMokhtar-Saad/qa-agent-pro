"""Updater OS-level file locking primitives (split from tools/updater.py)."""

from __future__ import annotations

import os
from typing import Any, Optional, cast

from tools.updater_log import logger

# Platform-conditional: fcntl is POSIX-only and msvcrt Windows-only, so each is None where absent.
try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = cast("Any", None)
try:
    import msvcrt
except ImportError:
    msvcrt = cast("Any", None)


class LockUnsupported(Exception):
    """Neither OS locking primitive is available on this interpreter."""


def _flock_nb(fd: int) -> Optional[bool]:
    """POSIX ``flock`` result, or ``None`` where ``fcntl`` is unavailable."""
    if fcntl is None:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


# Take an exclusive, NON-BLOCKING OS lock on *fd*. ``True`` if we got it.
#
# THE KERNEL OWNS LIVENESS. An advisory lock is released when the holding
# process exits -- cleanly, killed, or crashed -- so there is no such thing
# as a stale lock: nothing to detect, nothing to steal, and no check-then-act
# window in which two processes both decide to steal. Three review rounds of
# pid-and-timestamp bookkeeping were deleted rather than corrected, because
# each of their defects came from asking the lock FILE a question only the
# kernel can answer, and each fix for one direction opened the other.
#
# Non-blocking on purpose: this runs on the launcher's update poll, and a
# loser must skip the pass rather than hold the poll thread.
#
# WINDOWS, UNVERIFIED: no Windows machine has run this code, the same
# standing disclosure the mobile lane carries. `msvcrt.locking` locks a
# byte REGION from the current file position and that region has to
# EXIST, so this is not the POSIX call under another name -- the caller
# guarantees the file is at least one byte before we get here.
def os_lock(fd: int) -> bool:
    """Take an exclusive, NON-BLOCKING OS lock on *fd*. ``True`` if we got it."""
    got = _flock_nb(fd)
    if got is not None:
        return got
    if msvcrt is None:  # pragma: no cover - neither primitive exists
        raise LockUnsupported("neither fcntl nor msvcrt is available")
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        cast("Any", msvcrt).locking(fd, cast("Any", msvcrt).LK_NBLCK, 1)
        return True
    except OSError:
        return False


# Release what :func:`_os_lock` took. Never raises: the close below, and
# ultimately process exit, releases it regardless.
def _os_unlock(fd: int) -> None:
    """Release what :func:`_os_lock` took; never raises (close and exit release it)."""
    if fcntl is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            logger.debug("tools.updater: could not release the update lock")
        return
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        cast("Any", msvcrt).locking(fd, cast("Any", msvcrt).LK_UNLCK, 1)
    except (AttributeError, OSError):  # pragma: no cover - Windows only
        logger.debug("tools.updater: could not release the update lock")


def _try_os_lock(fd: int) -> Optional[bool]:
    """``_os_lock`` result, or ``None`` (warned) where locking is unsupported."""
    try:
        return os_lock(fd)
    except LockUnsupported as exc:  # pragma: no cover - unreachable on CPython
        # FAIL CLOSED. A skipped update costs one poll interval; a
        # concurrent overlay corrupts the install tree.
        logger.warning(
            "tools.updater: no OS file locking on this platform (%s), so an "
            "update cannot be serialised; skipping this pass.",
            exc,
        )
        return None


def _os_lock(fd: int) -> bool:
    """Legacy name of :func:`os_lock` (WX removes it)."""
    return os_lock(fd)
