"""Replay engine: the one report PNG of a stop (done, end of script, failed assert).

Fast steps (WP-D): a step takes NO picture. The screen is photographed only
where a run is judged, and the PNG goes to the run folder for the report --
never into a reply. Best-effort and never raises: a lost picture may not
change a verdict.
"""

from __future__ import annotations

import logging

from tools.mobile import adb, run_store
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
