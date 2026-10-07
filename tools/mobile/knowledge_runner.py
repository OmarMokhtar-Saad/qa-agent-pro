"""Off-the-hot-path runner: learn AFTER the run result was returned.

``after_result`` schedules a task that runs ``knowledge_learn.learn_run`` in a
worker thread; ``resume_pending`` does the same for earlier unlearned runs of the
package. One job at a time per package (bounded dict of locks). All errors are
swallowed and logged: learning must never affect a run or its reply.
"""

from __future__ import annotations

import asyncio
import logging

from tools.mobile import knowledge_learn, knowledge_limits, knowledge_run

logger = logging.getLogger(__name__)

_MAX_LOCKS = 64
_LOCKS: dict = {}
_TASKS: set = set()


def _lock_for(package: str) -> asyncio.Lock:
    lock = _LOCKS.get(package)
    if lock is None:
        if len(_LOCKS) >= _MAX_LOCKS:
            for key in [k for k, v in _LOCKS.items() if not v.locked()][:8]:
                _LOCKS.pop(key, None)
        lock = _LOCKS[package] = asyncio.Lock()
    return lock


async def _learn(package: str, run_id: str) -> dict:
    async with _lock_for(package):
        return await asyncio.to_thread(knowledge_learn.learn_run, package, run_id)


async def _job(package: str, run_id: str) -> None:
    try:
        got = await _learn(package, run_id)
        if got.get("error"):
            logger.info("knowledge learn %s: %s", run_id, got["error"])
    except BaseException as exc:  # includes CancelledError at shutdown
        logger.warning("knowledge learn job for %s ended: %r", run_id, exc)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise


async def after_result(package: str, run_id: str) -> None:
    """Schedule learning for *run_id* and return at once. Never raises."""
    try:
        if not package or not run_id:
            return
        task = asyncio.get_running_loop().create_task(_job(package, run_id))
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
    except Exception:
        logger.exception("knowledge runner: after_result failed")


async def resume_pending(package: str, exclude=()) -> list:
    """Learn up to PENDING_RESUME_LIMIT earlier runs, oldest first. Never raises."""
    done: list = []
    try:
        ids = await asyncio.to_thread(
            knowledge_run.pending_runs,
            package,
            exclude=tuple(exclude or ()),
            limit=knowledge_limits.PENDING_RESUME_LIMIT,
        )
        for run_id in ids:
            await _job(package, run_id)
            done.append(run_id)
    except Exception:
        logger.exception("knowledge runner: resume_pending failed")
    return done
