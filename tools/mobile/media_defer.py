"""Clips finished off the critical path: the pull runs in the background.

A step only has to STOP its recorder (one SIGINT) before the next one starts;
waiting for the container to close and pulling the file can overlap the next
step. Every deferred job is tracked per run so :func:`flush` can promise that
nothing is still in flight when the run reports.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from config.settings_guard import never_raise
from tools.mobile.media_args import ClipRef

logger = logging.getLogger("tools.mobile.media")

FLUSH_TIMEOUT_S = 60.0

_TASKS: dict[str, set[asyncio.Future[None]]] = {}
_OPEN: dict[tuple[str, str], int] = {}


def open_clips(run_id: str, tc_id: str) -> int:
    """How many deferred clips of this case have not been stored yet."""
    return _OPEN.get((run_id, tc_id), 0)


def _done(ref: ClipRef, task: asyncio.Future[None]) -> None:
    """Forget a finished job, so the counters never outlive the work."""
    key = (ref.run_id, ref.tc_id)
    left = max(0, _OPEN.get(key, 1) - 1)
    if left:
        _OPEN[key] = left
    else:
        _OPEN.pop(key, None)
    _TASKS.get(ref.run_id, set()).discard(task)


async def _guarded(coro: Coroutine[Any, Any, None]) -> None:
    """Run one deferred job; a failure is logged, never raised."""
    with never_raise(
        logger, "mobile.media deferred clip failed: %s", level=logging.WARNING
    ):
        await coro


def defer(ref: ClipRef, coro: Coroutine[Any, Any, None]) -> None:
    """Start *coro* in the background and count it against its case."""
    key = (ref.run_id, ref.tc_id)
    _OPEN[key] = _OPEN.get(key, 0) + 1
    task = asyncio.ensure_future(_guarded(coro))
    _TASKS.setdefault(ref.run_id, set()).add(task)
    task.add_done_callback(lambda t: _done(ref, t))


async def flush(run_id: str) -> None:
    """Wait (bounded) until every deferred clip of the run has been stored."""
    pending = set(_TASKS.get(run_id, ()))
    if pending:
        await asyncio.wait(pending, timeout=FLUSH_TIMEOUT_S)
