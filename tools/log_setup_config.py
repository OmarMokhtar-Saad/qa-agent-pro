"""Attach this process's own rotating file handler.

Part of the ``tools.log_setup`` facade; see its docstring for the design.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from tools.log_setup_base import (
    BACKUP_COUNT,
    DEFAULT_PREFIX,
    LOG_FORMAT,
    MAX_BYTES,
    process_log_path,
)
from tools.log_setup_client import ProcessContextFilter
from tools.log_setup_sweep import sweep_old_logs

if TYPE_CHECKING:
    from logging.handlers import RotatingFileHandler

logger = logging.getLogger("qa_agents.log_setup")


@dataclass(frozen=True)
class FileLoggingOptions:
    """The optional knobs of ``configure_file_logging``; ``None`` means the default."""

    max_bytes: int | None = None
    backup_count: int | None = None
    pid: int | None = None
    target: logging.Logger | None = None


def _attached(owner: logging.Logger, path: Path) -> logging.Handler | None:
    """The handler *owner* already has for *path*, or None."""
    for existing in list(owner.handlers):
        if getattr(existing, "qa_log_path", "") == str(path):
            return existing
    return None


def _open_handler(
    path: Path, level: int, opts: FileLoggingOptions
) -> RotatingFileHandler:
    """A configured rotating handler for *path* (budgets default to the module's)."""

    handler = RotatingFileHandler(
        str(path),
        maxBytes=MAX_BYTES if opts.max_bytes is None else int(opts.max_bytes),
        backupCount=(
            BACKUP_COUNT if opts.backup_count is None else int(opts.backup_count)
        ),
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(ProcessContextFilter())
    cast("Any", handler).qa_log_path = str(path)
    return handler


def _log_path(log_dir: str | Path, prefix: str, opts: FileLoggingOptions) -> Path:
    """Create the log directory and return this process's file path in it."""
    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    return process_log_path(directory, prefix=prefix, pid=opts.pid)


def _attach(
    path: Path, level: int, opts: FileLoggingOptions
) -> tuple[logging.Handler, bool]:
    """The handler for *path* on its owner logger, and whether it is new."""
    owner = opts.target if opts.target is not None else logging.getLogger()
    existing = _attached(owner, path)
    if existing is not None:
        return existing, False
    handler = _open_handler(path, level, opts)
    owner.addHandler(handler)
    return handler, True


# Attach this process's OWN rotating file handler and sweep old files.
#
# Returns the handler, or ``None`` when no file could be opened -- the caller
# degrades to INFO-on-stderr rather than losing the trail. Calling it twice for
# the same path returns the handler already attached, so a re-entrant startup
# cannot double every line. Never raises.
#
# *max_bytes* / *backup_count* are explicit so each caller keeps its own budget
# (the dist launcher stays at 1 MiB x 2, the server at 5 MiB x 3). *target*
# defaults to the root logger; tests pass their own so two simulated processes do
# not share one record stream.
def configure_file_logging(
    log_dir: str | Path,
    *,
    prefix: str = DEFAULT_PREFIX,
    level: int = logging.INFO,
    **options: Any,
) -> logging.Handler | None:
    """Attach this process's OWN rotating file handler and sweep old files."""
    opts = FileLoggingOptions(**options)  # an unknown keyword is a TypeError
    try:
        path = _log_path(log_dir, prefix, opts)
        handler, fresh = _attach(path, level, opts)
        if fresh:
            sweep_old_logs(path.parent, prefix=prefix, skip=path)
        return handler
    except (OSError, TypeError, ValueError):
        logger.debug("could not open a per-process log file", exc_info=True)
        return None
