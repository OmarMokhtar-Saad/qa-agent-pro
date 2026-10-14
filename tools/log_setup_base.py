"""Constants and file-name helpers shared by the per-process log leaves.

Part of the ``tools.log_setup`` facade; see its docstring for the design.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("qa_agents.log_setup")

DEFAULT_PREFIX = "qa-agents"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3
UNKNOWN_CLIENT = "unknown"
_MAX_CLIENT_CHARS = 40
LOG_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s "
    "[pid=%(process)d client=%(qa_client)s]: %(message)s"
)


def process_log_path(
    log_dir: str | Path,
    prefix: str = DEFAULT_PREFIX,
    pid: int | None = None,
) -> Path:
    """This process's own log file: ``<prefix>-<pid>.log``.

    *pid* is injectable so tests can model two processes without forking."""
    return Path(log_dir) / f"{prefix}-{os.getpid() if pid is None else pid}.log"


def _pid_from_name(name: str, prefix: str) -> int | None:
    """The pid encoded in ``<prefix>-<pid>.log[.N]``, or None if it does not parse."""
    try:
        stem = name[len(prefix) + 1 :].split(".", 1)[0]
        return int(stem)
    except (ValueError, OverflowError):
        return None
