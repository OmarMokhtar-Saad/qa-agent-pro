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
from typing import NamedTuple

from tools.mobile import (
    lock_reaper,
    media_pending,
    run_consts,
    run_finalize,
    run_idle_owner,
    run_idle_words,
    run_lease_core,
    run_list,
)
from tools.mobile.run_verdict import Verdict

logger = logging.getLogger("tools.mobile.session")

OUTCOME_UNFINISHED = "unfinished"
#: Newest runs the dead-server sweep looks at.
SWEEP_LIMIT = 20
#: Stop codes whose report line carries the recorded reason as well.
STOP_WORDED = (
    run_finalize.STOP_IDLE_TIMEOUT,
    run_finalize.STOP_SERVER_DIED,
    run_finalize.STOP_ORPHANED,
)
_WORDS = {
    run_finalize.STOP_IDLE_TIMEOUT: run_idle_words.idle_reason,
    run_finalize.STOP_ORPHANED: run_idle_words.orphaned_reason,
}


async def close_run(run_id: str, stop: str, ports: SimpleNamespace) -> bool:
    """End *run_id* as *stop*: verdict through the one funnel, then free its device."""
    progress = run_idle_words.progress_of(run_id)
    text = _WORDS.get(stop, run_idle_words.died_reason)(progress, time.time())
    verdict = Verdict(OUTCOME_UNFINISHED, text, run_idle_words.evidence_of(progress))
    try:
        await lock_reaper.call_port(ports.stop_heartbeat, run_id)
        wrote = run_finalize.finalize(run_id, verdict, stop=stop)
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


def _lease_of(run_id: str) -> dict:
    return (run_lease_core.read_lease(run_id) or {}).get("content") or {}


def _owner_up(run_id: str) -> bool:
    """The lease owner's process is still running (a sibling server's run)."""
    return run_idle_owner.owner_alive(_lease_of(run_id))


def _stop_for(run_id: str) -> str:
    """``server_died`` only when the lease owner's process is really gone."""
    if _owner_up(run_id):
        return run_finalize.STOP_ORPHANED
    return run_finalize.STOP_SERVER_DIED


class Sweep(NamedTuple):
    """One sweep: the caller's own run, the close ports, and whether a live owner is spared."""

    owner: str
    ports: SimpleNamespace
    only_dead: bool = False


async def _sweep_row(row: dict, sweep: Sweep) -> bool:
    """Close *row* when its server died; one bad row never ends the sweep."""
    ports, only_dead = sweep.ports, sweep.only_dead
    try:
        alive = row["run_id"] in ports.live_runs()
        if alive or not _is_dead(row, sweep.owner, time.time()):
            return False
        if only_dead and _owner_up(row["run_id"]):
            return False
        media_pending.restore(row["run_id"])
        return await close_run(row["run_id"], _stop_for(row["run_id"]), ports)
    except (OSError, ValueError, TypeError, KeyError):
        logger.warning("run_idle_close: row skipped", exc_info=True)
        return False


async def close_dead_runs(
    owner: str, ports: SimpleNamespace, *, only_dead: bool = False
) -> list[str]:
    """Close every recent run whose server died. Never raises; the closed ids.

    ``only_dead`` spares a run whose lease owner process still runs (start-up:
    that is a live sibling server's run, not ours to close).
    """
    sweep = Sweep(owner, ports, only_dead)
    closed: list[str] = []
    try:
        rows = (run_list.list_runs(SWEEP_LIMIT, gc=False) or {}).get("content") or []
        for row in rows:
            if await _sweep_row(row, sweep):
                closed.append(row["run_id"])
    except (OSError, ValueError, TypeError, KeyError):
        logger.warning("run_idle_close: dead-run sweep skipped", exc_info=True)
    return closed
