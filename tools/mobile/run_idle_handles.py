"""The live idle-timer handles of this process, kept apart so a run's end can cancel its own."""

from __future__ import annotations

import asyncio

TIMERS: dict[str, asyncio.TimerHandle] = {}


def cancel(run_id: str) -> None:
    """Forget (and cancel) the idle timer of *run_id*."""
    handle = TIMERS.pop(run_id, None)
    if handle is not None:
        handle.cancel()
