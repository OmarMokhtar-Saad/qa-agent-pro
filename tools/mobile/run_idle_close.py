"""Close a run the server itself found abandoned: idle for too long, or orphaned.

Two paths, ONE funnel (``run_finalize.finalize``): a per-run idle timer ends a
run that went quiet (``idle_timeout``), and a sweep ends a run whose server died
(``server_died``: no ``final`` and a lease that stopped beating). The verdict
outcome is ``unfinished``, and its reason says what happened, truthfully.
``released_idle`` stays for the path where a NEW run took the device.
"""

from __future__ import annotations

import logging
import math
import time
from types import SimpleNamespace

from tools.mobile import (
    lock_reaper,
    media_pending,
    run_consts,
    run_finalize,
    run_lease_core,
    run_list,
    run_store,
)
from tools.mobile.run_verdict import Verdict

logger = logging.getLogger("tools.mobile.session")

OUTCOME_UNFINISHED = "unfinished"
#: Newest runs the dead-server sweep looks at.
SWEEP_LIMIT = 20
#: Stop codes whose report line carries the recorded reason as well.
STOP_WORDED = (run_finalize.STOP_IDLE_TIMEOUT, run_finalize.STOP_SERVER_DIED)


def steps_taken(run_id: str) -> int:
    """Steps the run's cases recorded in total."""
    cases = (run_store.list_cases(run_id) or {}).get("content") or []
    return sum(len(c.get("trace") or []) for c in cases if isinstance(c, dict))


def _clock(now: float) -> str:
    return time.strftime("%H:%M", time.localtime(now))


def idle_reason(steps: int, now: float) -> str:
    """The report wording of a run the idle timer closed."""
    return (
        "went idle after step "
        + str(steps)
        + ", closed by the server at "
        + _clock(now)
    )


def died_reason(steps: int, now: float) -> str:
    """The report wording of a run whose server died."""
    return (
        "the server stopped after step "
        + str(steps)
        + "; closed by a later session on "
        + time.strftime("%Y-%m-%d", time.localtime(now))
        + " at "
        + _clock(now)
    )


async def close_run(run_id: str, stop: str, ports: SimpleNamespace) -> bool:
    """End *run_id* as *stop*: verdict through the one funnel, then free its device."""
    now = time.time()
    steps = steps_taken(run_id)
    text = (
        idle_reason(steps, now)
        if stop == run_finalize.STOP_IDLE_TIMEOUT
        else died_reason(steps, now)
    )
    try:
        await lock_reaper.call_port(ports.stop_heartbeat, run_id)
        wrote = run_finalize.finalize(
            run_id, Verdict(OUTCOME_UNFINISHED, text), stop=stop
        )
        await lock_reaper.call_port(ports.release, ports.serial_of(run_id), run_id)
    except lock_reaper.PORT_FAILURES:
        logger.warning("run_idle_close: could not close %s", run_id, exc_info=True)
        return False
    lock_reaper.forget(run_id)
    return wrote


def _is_dead(row: dict, owner: str, now: float) -> bool:
    """No ``final``, a lease that stopped beating and was never released."""
    manifest = row.get("manifest") or {}
    if not manifest or manifest.get("final") or row["run_id"] == owner:
        return False
    lease = (run_lease_core.read_lease(row["run_id"]) or {}).get("content")
    if not isinstance(lease, dict) or lease.get("released_at"):
        return False
    return now - _beat(lease) > run_consts.LEASE_STALE_S


def _beat(lease: dict) -> float:
    """The lease heartbeat; junk or non-finite reads as 0.0, i.e. long stale."""
    try:
        beat = float(lease.get("heartbeat") or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return beat if math.isfinite(beat) else 0.0


async def _sweep_row(row: dict, owner: str, ports: SimpleNamespace) -> bool:
    """Close *row* when its server died; one bad row never ends the sweep."""
    try:
        alive = row["run_id"] in ports.live_runs()
        if alive or not _is_dead(row, owner, time.time()):
            return False
        media_pending.restore(row["run_id"])
        return await close_run(row["run_id"], run_finalize.STOP_SERVER_DIED, ports)
    except (OSError, ValueError, TypeError, KeyError):
        logger.warning("run_idle_close: row skipped", exc_info=True)
        return False


async def close_dead_runs(owner: str, ports: SimpleNamespace) -> list[str]:
    """Close every recent run whose server died. Never raises; the closed ids."""
    closed: list[str] = []
    try:
        rows = (run_list.list_runs(SWEEP_LIMIT, gc=False) or {}).get("content") or []
        for row in rows:
            if await _sweep_row(row, owner, ports):
                closed.append(row["run_id"])
    except (OSError, ValueError, TypeError, KeyError):
        logger.warning("run_idle_close: dead-run sweep skipped", exc_info=True)
    return closed
