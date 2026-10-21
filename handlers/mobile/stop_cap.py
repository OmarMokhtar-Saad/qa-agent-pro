"""``qa_mobile_stop`` never keeps the host waiting on a lock or a slow close (fix/1580 C13).

The stop itself (verdict write, heartbeat, lease release) is shielded and goes on
past the cap; only the REPLY is bounded, so closing is unchanged.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable

from tools.mobile.stop_run import StopResult

logger = logging.getLogger("qa_agents.mcp")
_PENDING: set[asyncio.Future[StopResult]] = set()

#: Longest the stop reply waits; under the host's ~120 s tool timeout.
STOP_REPLY_CAP_S = 60.0

PENDING_HEAD = "Stop pending"
_SLOWER = (
    PENDING_HEAD + ": the stop is not confirmed yet and is still finishing in the "
    "background (the run's own timer closes it either way). Do not repeat the stop: call "
    "`qa_mobile_status` in a minute to see the run end."
)


def _reap(task: asyncio.Future[StopResult]) -> None:
    """Forget a finished stop task; log a failure nobody awaits any more."""
    _PENDING.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.error("qa_mobile_stop failed", exc_info=task.exception())


async def capped(stopping: Awaitable[StopResult]) -> StopResult:
    """The stop's result, or a not-yet reply once the cap passes."""
    task = asyncio.ensure_future(stopping)
    _PENDING.add(task)
    task.add_done_callback(_reap)
    try:
        return await asyncio.wait_for(asyncio.shield(task), STOP_REPLY_CAP_S)
    except asyncio.TimeoutError:
        return StopResult(False, _SLOWER)
