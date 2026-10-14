"""Advisory lock and atomic write of tools.client_registry (split out)."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, cast

# fcntl is POSIX-only: where it is absent the lock is skipped (best effort).
try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = cast("Any", None)

logger = logging.getLogger("qa_agents.client_registry")


def _lock(path: Path):
    """Advisory exclusive lock on a sidecar file. Returns an fd, or None.

    A sidecar (not the config itself) so the lock survives the ``os.replace`` that
    swaps the config inode. Best effort: a platform without ``fcntl`` proceeds
    unlocked rather than refusing to register.
    """
    if fcntl is None:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd
    except (OSError, ValueError):
        logger.debug("could not acquire an MCP-config lock; proceeding", exc_info=True)
        return None


def _unlock(fd) -> None:
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except (OSError, ValueError):
        logger.debug("could not release the MCP-config lock", exc_info=True)
    finally:
        try:
            os.close(fd)
        except OSError:
            logger.debug("could not close the MCP-config lock fd", exc_info=True)


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temp file in the SAME directory, then ``os.replace``.

    Same-directory matters: ``os.replace`` is only atomic within one filesystem.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="." + path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
