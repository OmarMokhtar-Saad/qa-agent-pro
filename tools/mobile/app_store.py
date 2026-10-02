"""Bounded, atomic JSON files in one app's own directory (``apps/<package>/``).

The directory is the one ``app_knowledge`` already uses, so the package-name check, the
case-folding rule and the cache root are shared, not copied. The package is device-supplied
and therefore untrusted: ``app_knowledge.db_path`` admits only an Android package name
(letters, digits, underscore and dot), and this module still refuses a directory that is a
symlink or that resolves anywhere but directly under ``apps/``.

Every public function returns an error/content dict (or an empty dict for a read) and never
raises. Two chats saving at once is last-write-wins per file; the in-process lock only keeps
this server's own read-modify-write whole.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import re
import secrets
import stat
import tempfile
import threading

from tools.mobile import app_knowledge, paths

logger = logging.getLogger(__name__)

#: Bytes one stored file may hold, on write AND on read. A larger file is refused, never
#: truncated.
MAX_FILE_BYTES = 262144

_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,39}[.]json")
_LOCK = threading.Lock()

#: True when every step can run relative to an already-opened, symlink-refused directory
#: handle. Windows has none of these calls and keeps the path code, whose check-then-use
#: window is stated, not closed. ``os.replace`` shares ``os.rename``'s ``renameat`` and
#: takes the same dir_fd keywords, so either one in ``supports_dir_fd`` is enough.
_PINNED = (
    os.name != "nt"
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and os.open in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
    and (os.replace in os.supports_dir_fd or os.rename in os.supports_dir_fd)
)


def _race_window(stage: str) -> None:
    """Test seam, a no-op in production: called at ``"validated"`` (the path was accepted,
    nothing opened yet) and ``"opened"`` (the use is about to start), the two instants a
    local process could swap the directory for a symlink."""


def _err(message: str) -> dict:
    return {"error": message, "content": None}


def app_dir(package: object):
    """``apps/<package>/`` for a valid package name, else ``None``. Creates nothing."""
    try:
        found = app_knowledge.db_path(package)
        if found is None:
            return None
        target = found.parent
        root = paths.sub("apps").resolve()
        if target.is_symlink() or target.resolve().parent != root:
            return None
        return target
    except Exception:
        logger.exception("mobile.app_store.app_dir failed")
        return None


def _file(package: object, name: object):
    directory = app_dir(package)
    if directory is None or not _NAME_RE.fullmatch(str(name or "")):
        return None
    return directory / str(name)


def _open_dir(directory) -> int:
    """A handle on *directory* itself; a symlink at that name is refused (``OSError``)."""
    return os.open(str(directory), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def _read_capped(dir_fd: int, name: str):
    """Up to ``MAX_FILE_BYTES + 1`` bytes of the regular file *name*, else ``None``."""
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
        return handle.read(MAX_FILE_BYTES + 1)


def _decode(raw) -> dict:
    if raw is None or len(raw) > MAX_FILE_BYTES:
        return {}
    body = json.loads(raw.decode("utf-8"))
    return body if isinstance(body, dict) else {}


def _read_pinned(directory, name: str) -> dict:
    try:
        dir_fd = _open_dir(directory)
    except OSError:
        return {}
    try:
        _race_window("opened")
        return _decode(_read_capped(dir_fd, name))
    finally:
        os.close(dir_fd)


def _read_path(target) -> dict:
    """The check-then-use read, for platforms without handle-relative calls."""
    if target.is_symlink() or not target.is_file():
        return {}
    if target.stat().st_size > MAX_FILE_BYTES:
        return {}
    _race_window("opened")
    body = json.loads(target.read_text(encoding="utf-8"))
    return body if isinstance(body, dict) else {}


def _publish(dir_fd: int, name: str, raw: bytes) -> None:
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


def _write_pinned(directory, name: str, raw: bytes) -> dict:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        dir_fd = _open_dir(directory)
    except OSError:
        return _err("the app directory is a symlink or is not a directory")
    try:
        _race_window("opened")
        try:
            os.fchmod(dir_fd, 0o700)
        except OSError:
            pass
        _publish(dir_fd, name, raw)
    finally:
        os.close(dir_fd)
    return {"error": None, "content": {"bytes": len(raw)}}


def _write_path(target, raw: bytes) -> dict:
    """The check-then-use write, for platforms without handle-relative calls."""
    directory = target.parent
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink():
        return _err("the app directory is a symlink")
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    _race_window("opened")
    fd, tmp = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(directory)
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return {"error": None, "content": {"bytes": len(raw)}}


def read_json(package: object, name: object) -> dict:
    """The stored object, or ``{}`` for a missing, oversized, corrupt or non-object file."""
    try:
        target = _file(package, name)
        if target is None:
            return {}
        _race_window("validated")
        if _PINNED:
            return _read_pinned(target.parent, target.name)
        return _read_path(target)
    except Exception:
        logger.warning("mobile.app_store.read_json failed", exc_info=True)
        return {}


def write_json(package: object, name: object, body: object) -> dict:
    """Write *body* atomically: temp file in the same directory, fsync, 0600, ``os.replace``.

    The directory is made 0700. A body over ``MAX_FILE_BYTES`` is refused and the old file
    is left alone; a failure after the temp file exists removes it.
    """
    try:
        target = _file(package, name)
        if target is None or not isinstance(body, dict):
            return _err("not a valid package, file name or body")
        raw = json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(raw) > MAX_FILE_BYTES:
            return _err("the stored file would exceed its %d byte cap" % MAX_FILE_BYTES)
        _race_window("validated")
        if _PINNED:
            return _write_pinned(target.parent, target.name, raw)
        return _write_path(target, raw)
    except Exception as exc:
        logger.exception("mobile.app_store.write_json failed")
        return _err("could not write the file (%s)" % type(exc).__name__)


def update_json(package: object, name: object, mutate) -> dict:
    """Read, let ``mutate(body)`` change the dict in place, write it back.

    ``mutate`` returns ``''`` to proceed or a refusal text, which is returned as the error
    with nothing written. The three steps run under one in-process lock.
    """
    with _LOCK:
        body = read_json(package, name)
        try:
            problem = mutate(body)
        except Exception:
            logger.exception("mobile.app_store.update_json mutate failed")
            return _err("the update failed")
        if problem:
            return _err(str(problem))
        return write_json(package, name, body)
