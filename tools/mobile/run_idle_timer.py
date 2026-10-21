"""The per-run idle timer: a run nobody drives closes ITSELF after 300 s.

``arm`` is called on every step and re-arms one asyncio timer per run. When it
fires and the run really has been quiet for ``IDLE_RELEASE_TIMEOUT_S``, the run
is closed as ``idle_timeout`` through ``run_finalize.finalize`` and its device
is freed. A run paused for the tester is not abandoned, so it is only re-armed.
The first arm of a process also sweeps runs whose server died.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

from tools.mobile import (
    lock_reaper,
    run_finalize,
    run_idle_close,
    run_idle_handles,
    run_store,
    session_finish,
)

logger = logging.getLogger("tools.mobile.session")

_TASKS: set[asyncio.Task[object]] = set()
_SWEPT: list[bool] = []


def _spawn(coro: Coroutine[Any, Any, object]) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


def _schedule(run_id: str, delay: float) -> None:
    loop = asyncio.get_running_loop()
    run_idle_handles.TIMERS[run_id] = loop.call_later(delay, _fire, run_id)


cancel = run_idle_handles.cancel


def arm(run_id: str) -> None:
    """(Re)start the idle countdown of *run_id*. No loop running: a no-op."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("run_idle_timer: no running loop, %s not armed", run_id)
        return
    cancel(run_id)
    _schedule(run_id, lock_reaper.IDLE_RELEASE_TIMEOUT_S)
    if not _SWEPT:
        _SWEPT.append(True)
        _spawn(run_idle_close.close_dead_runs(run_id, session_finish.timeout_ports()))


def _fire(run_id: str) -> None:
    run_idle_handles.TIMERS.pop(run_id, None)
    _spawn(_expire(run_id))


async def _expire(run_id: str) -> None:
    """Close *run_id* when it is idle, else wait out what is left."""
    if run_finalize.recorded(_manifest(run_id)):  # a recorded verdict is final
        return
    idle = lock_reaper.idle_seconds(run_id)
    limit = lock_reaper.IDLE_RELEASE_TIMEOUT_S
    if idle is None or session_finish.awaits_tester(run_id):
        _schedule(run_id, limit)
    elif idle < limit - 1:
        _schedule(run_id, limit - idle)
    else:
        await run_idle_close.close_run(
            run_id, run_finalize.STOP_IDLE_TIMEOUT, session_finish.timeout_ports()
        )


def _manifest(run_id: str) -> dict:
    return run_store.read_manifest(run_id).get("content") or {}
