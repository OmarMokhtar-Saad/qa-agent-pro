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

import json
import logging
import os
import re
import secrets  # noqa: F401 - tests patch app_store.secrets.token_hex
import threading

from config.settings_guard import never_raise
from tools.mobile import app_knowledge, paths
from tools.mobile.app_store_io import (
    Job,
    read_path,
    read_pinned,
    write_path,
    write_pinned,
)
from tools.mobile.app_store_io import (
    err as _err,
)

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


def race_window(stage: str) -> None:
    """Test seam, a no-op in production: called at ``"validated"`` (the path was accepted,
    nothing opened yet) and ``"opened"`` (the use is about to start), the two instants a
    local process could swap the directory for a symlink."""


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


def read_json(package: object, name: object) -> dict:
    """The stored object, or ``{}`` for a missing, oversized, corrupt or non-object file."""
    with never_raise(
        logger,
        "mobile.app_store.read_json failed",
        level=logging.WARNING,
        exc_info=True,
    ):
        target = _file(package, name)
        if target is None:
            return {}
        race_window("validated")
        job = Job(target, MAX_FILE_BYTES, race_window)
        return read_pinned(job) if _PINNED else read_path(job)
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
        race_window("validated")
        job = Job(target, MAX_FILE_BYTES, race_window)
        return write_pinned(job, raw) if _PINNED else write_path(job, raw)
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
