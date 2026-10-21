"""Event-loop stall detector: logs when the loop did not run for > threshold.

A periodic 1 s tick measures how late the loop wakes it. A sync adb/file call
inside an async handler blocks every other call on the connection, which looks
exactly like a lost call; this line tells delayed from lost. Diagnostics only.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import anyio

TICK_S = 1.0
STALL_S = 2.0
logger = logging.getLogger("qa_agents.mcp")


def stall_seconds(expected: float, actual: float) -> float:
    """Lag of one tick: how much later than ``expected`` the loop woke."""
    return max(0.0, actual - expected)


async def watch_loop(
    tick: float = TICK_S,
    threshold: float = STALL_S,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Run forever inside the event loop; cancel it to stop."""
    while True:
        before = clock()
        await anyio.sleep(tick)
        lag = stall_seconds(tick, clock() - before)
        if lag > threshold:
            logger.warning("event loop stalled %.1f s", lag)
