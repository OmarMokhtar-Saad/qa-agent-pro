"""Updater cross-process update lock context manager (split from tools/updater.py)."""

from __future__ import annotations

import contextlib
import os
import secrets
import time
from pathlib import Path
from typing import Optional

from tools.updater_lockdiag import (
    _log_lock_contention,
    _warn_lock_is_dir,
    _warn_open_failed,
)
from tools.updater_log import logger
from tools.updater_oslock import _os_unlock, _try_os_lock

_UPDATE_LOCK_FILE = ".update.lock"


def _stamp_lock(fd: int) -> None:
    """Ours. Record who we are, for the log line in some OTHER process --
    never for a decision here."""
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        stamp = str(os.getpid()) + ":" + secrets.token_hex(8) + " " + str(time.time())
        os.write(fd, stamp.encode("utf-8"))
    except OSError:
        logger.debug("tools.updater: could not stamp the update lock")


def _close_quietly(fd: int) -> None:
    """Close *fd*, ignoring errors."""
    try:
        os.close(fd)
    except OSError:
        pass


# Open (creating) the lock file; ``None`` after a warning if that fails.
# A descriptor opened before a later step fails is closed here, so the
# caller never holds one it was not handed.
def _open_lock_fd(lock_path: Path, install_dir: Path) -> Optional[int]:
    """Open (creating) the lock file; ``None`` after a warning if that fails."""
    if lock_path.is_dir():
        _warn_lock_is_dir(lock_path)
        return None
    fd = None
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        # The region msvcrt will lock has to exist, and an empty file has
        # no byte 0. Written BEFORE the lock and overwritten after it, so
        # the identity in the file is always the winner's.
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        return fd
    except OSError as exc:
        _warn_open_failed(install_dir, exc)
        if fd is not None:
            _close_quietly(fd)
        return None


def _release_lock_fd(fd: Optional[int], locked: bool) -> None:
    """Unlock (when held) and close the lock descriptor."""
    if fd is None:
        return
    if locked:
        _os_unlock(fd)
    _close_quietly(fd)


def _report_contention(
    locked: Optional[bool], lock_path: Path, install_dir: Path
) -> None:
    """Log contention, unless locking itself was unsupported (already warned)."""
    if locked is not None:
        _log_lock_contention(lock_path, install_dir)


# Serialise ``apply_update`` ACROSS PROCESSES with an OS lock.
# Three launcher pids on the same install_dir can start an update at once --
# observed 2026-09-03: two of three aborted with "release tree does not match
# its MANIFEST.sha256", because the swap is not atomic against a concurrent
# overlay.
#
# Yields ``True`` when the lock was taken (released on exit from this
# context), ``False`` otherwise -- and never raises for any of it. A caller
# that cannot even reach the lock is contention's twin: a read-only or absent
# install_dir, or a directory sitting on the lock path, must cost one update
# poll rather than the launcher.
#
# The lock FILE is never unlinked and never read to make a decision. It
# exists so the kernel has something to lock and so a human has something to
# read; its contents are diagnostics.
@contextlib.contextmanager
def _update_lock(install_dir: Path):
    """Serialise ``apply_update`` across processes; yields whether we hold it."""
    lock_path = install_dir / _UPDATE_LOCK_FILE
    fd: Optional[int] = None
    locked: Optional[bool] = False
    try:
        fd = _open_lock_fd(lock_path, install_dir)
        if fd is not None:
            locked = _try_os_lock(fd)
            if locked:
                _stamp_lock(fd)
            else:
                _report_contention(locked, lock_path, install_dir)
        yield bool(locked)
    finally:
        _release_lock_fd(fd, bool(locked))
