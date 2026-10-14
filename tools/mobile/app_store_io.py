"""The file-level steps behind ``app_store``: handle-relative reads and writes.

``app_store`` owns the policy (package/name checks, the byte cap, the race-window seam) and
hands each call a ``Job`` carrying the target path, the cap and the seam, read at call time
so a test that patches the facade's cap or seam still reaches these functions.
"""

from __future__ import annotations

import errno
import json
import os
import secrets
import stat
import tempfile
from pathlib import Path
from typing import Callable, NamedTuple


class Job(NamedTuple):
    """One read or write: the target file, the byte cap and the race-window seam."""

    target: Path
    cap: int
    race: Callable[[str], None]


def err(message: str) -> dict:
    return {"error": message, "content": None}


def saved(raw: bytes) -> dict:
    return {"error": None, "content": {"bytes": len(raw)}}


def open_dir(directory) -> int:
    """A handle on *directory* itself; a symlink at that name is refused (``OSError``)."""
    return os.open(str(directory), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def read_capped(dir_fd: int, name: str, cap: int):
    """Up to ``cap + 1`` bytes of the regular file *name*, else ``None``."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return None
        raise
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            return None
        return handle.read(cap + 1)


def decode(raw, cap: int) -> dict:
    if raw is None or len(raw) > cap:
        return {}
    body = json.loads(raw.decode("utf-8"))
    return body if isinstance(body, dict) else {}


def read_pinned(job: Job) -> dict:
    try:
        dir_fd = open_dir(job.target.parent)
    except OSError:
        return {}
    try:
        job.race("opened")
        return decode(read_capped(dir_fd, job.target.name, job.cap), job.cap)
    finally:
        os.close(dir_fd)


def read_path(job: Job) -> dict:
    """The check-then-use read, for platforms without handle-relative calls."""
    target = job.target
    if target.is_symlink() or not target.is_file():
        return {}
    if target.stat().st_size > job.cap:
        return {}
    job.race("opened")
    body = json.loads(target.read_text(encoding="utf-8"))
    return body if isinstance(body, dict) else {}


def publish(dir_fd: int, name: str, raw: bytes) -> None:
    """Temp file, fsync, ``os.replace``: all relative to the checked directory handle."""
    tmp = "%s.%s.tmp" % (name, secrets.token_hex(8))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(tmp, flags, 0o600, dir_fd=dir_fd)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=dir_fd)
        except OSError:
            pass
        raise


def write_pinned(job: Job, raw: bytes) -> dict:
    directory = job.target.parent
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        dir_fd = open_dir(directory)
    except OSError:
        return err("the app directory is a symlink or is not a directory")
    try:
        job.race("opened")
        try:
            os.fchmod(dir_fd, 0o700)
        except OSError:
            pass
        publish(dir_fd, job.target.name, raw)
    finally:
        os.close(dir_fd)
    return saved(raw)


def _chmod_quiet(path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _unlink_quiet(tmp) -> None:
    try:
        os.unlink(tmp)
    except OSError:
        pass


def _stage_and_replace(directory, target, raw: bytes) -> None:
    fd, tmp = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(directory)
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        _chmod_quiet(tmp, 0o600)
        os.replace(tmp, target)
    except BaseException:
        _unlink_quiet(tmp)
        raise


def write_path(job: Job, raw: bytes) -> dict:
    """The check-then-use write, for platforms without handle-relative calls."""
    directory = job.target.parent
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink():
        return err("the app directory is a symlink")
    _chmod_quiet(directory, 0o700)
    job.race("opened")
    _stage_and_replace(directory, job.target, raw)
    return saved(raw)
