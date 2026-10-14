"""Where a package's knowledge store lives, and its owner-only modes."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

from tools.mobile import appk_consts, paths

logger = logging.getLogger("tools.mobile.app_knowledge")


def db_path(package: Any) -> Path | None:
    """Where *package*'s store lives, or ``None`` for a name that is not an
    Android package name. Pure: creates nothing."""
    name = str(package or "").strip()
    if len(name) > appk_consts._FIELD_CHARS or not appk_consts._PACKAGE_RE.match(name):
        return None
    return paths.sub("apps") / _dir_name(name) / "knowledge.db"


def _dir_name(name: str) -> str:
    """The store directory for package *name*. Android package names are
    case-sensitive but macOS and Windows file systems are not, so
    ``com.Foo.app`` and ``com.foo.app`` would share one store there. A name
    with an upper-case letter gets a lower-case directory plus a digest of the
    exact name; an all-lower-case name (the usual case) is used as is.
    A mixed-case store made before this rule is left where it was: no longer
    read, never deleted."""
    if name == name.lower():
        return name
    return "%s~%s" % (name.lower(), hashlib.sha256(name.encode()).hexdigest()[:12])


def _store_path(package: Any) -> Path | None:
    """``db_path``, plus owner-only modes on a mixed-case store made before the
    ``_dir_name`` rule, which still sits under the exact package name. That
    store is never read again, but its notes stay on disk. Creates nothing."""
    path = db_path(package)
    if path is not None:
        legacy = path.parent.with_name(str(package).strip()) / path.name
        if legacy.parent != path.parent and legacy.parent.is_dir():
            tighten_store(legacy)
    return path


def _tighten(path: Path, mode: int) -> None:
    """Owner-only *mode* where the OS allows it. Notes are plain text about the
    tester's app; nobody else on the machine needs to read them."""
    try:
        path.chmod(mode)
    except OSError:
        logger.info("app knowledge: could not tighten permissions on %s", path)


def tighten_store(path: Path) -> None:
    """Owner-only modes on *path*'s directory, the database and its ``-wal`` /
    ``-shm`` files, so a store made before the mode rule is fixed by any open,
    not only the next write. Creates nothing and never raises: a path with no
    file name (``/``) is left alone."""
    if not path.name:
        return
    _tighten(path.parent, 0o700)
    for name in (path.name, path.name + "-wal", path.name + "-shm"):
        member = path.with_name(name)
        if member.exists():
            _tighten(member, 0o600)


#: The ``app_knowledge`` facade still re-exports the private name.
_tighten_store = tighten_store
