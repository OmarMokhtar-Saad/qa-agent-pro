"""Exit when the process that spawned this server is gone (no orphan servers)."""

from __future__ import annotations

import logging
import os
from typing import Callable

import anyio

logger = logging.getLogger(__name__)

#: Upper bound on how long an orphaned server outlives its parent.
PARENT_POLL_S = 5.0


def parent_gone(parent_pid: int) -> bool:
    """True once the original parent is no longer our parent (we were re-parented)."""
    return os.name != "nt" and os.getppid() != parent_pid


def _exit_now() -> None:
    logger.info("Parent process is gone - this server exits.")
    os._exit(0)


async def watch_parent(
    parent_pid: int | None = None, on_gone: Callable[[], None] = _exit_now
) -> None:
    """Poll our parent; stdin EOF is the fast path, this covers a held-open pipe."""
    pid = os.getppid() if parent_pid is None else parent_pid
    while not parent_gone(pid):
        await anyio.sleep(PARENT_POLL_S)
    on_gone()
