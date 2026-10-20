"""Replay engine: the one report PNG of a stop (done, end of script, failed assert).

Fast steps (WP-D): a step takes NO picture. The screen is photographed only
where a run is judged, and the PNG goes to the run folder for the report --
never into a reply. Best-effort and never raises: a lost picture may not
change a verdict.
"""

from __future__ import annotations

import asyncio
import logging

from tools.mobile import adb, run_store, shot_pending
from tools.mobile.replay.context import _ReplayRun

logger = logging.getLogger("tools.mobile.executor")


async def stop_shot(run: _ReplayRun, screen: dict | None) -> None:
    """Write ONE PNG of *screen* into the run folder, keyed by its observation."""
    run_id = str(run.ctx.run_id or "")
    obs_id = run_store.observation_id(screen)
    if not (run_id and obs_id and run.ctx.serial):
        return
    try:
        shot = await adb.screencap(run.ctx.serial)
        data = shot.get("content") if not shot.get("error") else None
        if data:
            run_store.write_shot(run_id, obs_id, data)
    except Exception:  # never-raise: a picture is not a verdict
        logger.exception("mobile.replay stop shot failed")


# B11: the picture is only for the report, so the step reply need not wait for
# it. It runs as a task; any later adb call on the device settles it first
# (``adb_run.run_argv``), so it never overlaps the next actuation. Never cancelled.
async def _shot_task(run: _ReplayRun, screen: dict | None) -> None:
    shot_pending.inside()  # its own adb calls must not wait on itself
    await stop_shot(run, screen)


def defer_shot(run: _ReplayRun, screen: dict | None) -> None:
    """Start :func:`stop_shot` in the background; :func:`drain` awaits it."""
    serial = str(run.ctx.serial or "")
    shot_pending.track(serial, asyncio.ensure_future(_shot_task(run, screen)))


async def drain(serial: str) -> None:
    """Wait (bounded) for every deferred picture of *serial* (they never raise)."""
    await shot_pending.settle(serial)


async def drain_all() -> None:
    """Wait for every deferred picture on every device (the report reads PNGs)."""
    await shot_pending.settle_all()
