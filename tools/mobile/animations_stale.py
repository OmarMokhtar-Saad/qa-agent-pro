"""The sweep that gives back animation scales a crashed run left off on a device."""

from __future__ import annotations

import logging

from tools.mobile import ime_session, run_records
from tools.mobile import locks as mobile_locks
from tools.mobile.animations_record import clear_record, read_record
from tools.mobile.animations_shell import _restore_command, _run

logger = logging.getLogger("tools.mobile.animations")


def stale_records(serial: str, *, skip_run_id: str = "") -> list:
    """Records from OTHER runs that still claim to have changed *serial*."""
    return run_records.stale_records(
        run_records.StaleScan(
            serial,
            skip_run_id,
            ime_session.IME_STALE_SCAN_RUNS,
            read_record,
            logger,
            "mobile.animations",
        )
    )


def _stale_outcome(restored: bool, detail: str, runs: list) -> dict:
    return {
        "error": None,
        "content": {"restored": restored, "detail": detail, "runs": runs},
    }


def _stale_blockers(serial: str, skip_run_id: str, found: list) -> list | None:
    """Run ids that stop a restore, or None when it is safe to go ahead."""

    rows = mobile_locks.device_holders([str(serial)]) or []
    row = rows[0] if rows else {}
    mine = str(skip_run_id or "")
    held_by_another = bool(row.get("held")) and str(row.get("owner") or "") != mine
    undead = [
        r["run_id"]
        for r in found
        if r.get("ended") is not True
        and not ime_session.pid_is_provably_dead(r.get("pid"))
    ]
    if not rows or row.get("error") or held_by_another or undead:
        return undead or [r["run_id"] for r in found]
    return None


async def _give_back_oldest(serial: str, found: list) -> dict:
    # OLDEST first: the earliest record holds the tester's own values.
    found.sort(key=lambda r: float(r.get("recorded_at") or 0.0))
    result = await _run(serial, _restore_command(found[0].get("previous") or {}))
    runs = [r["run_id"] for r in found]
    if result.get("error"):
        detail = (
            "could not restore the animation scales an earlier run "
            "left off (" + str(result["error"])[:160] + "). It will "
            "be tried again next time."
        )
        return _stale_outcome(False, detail, runs)
    for record in found:
        clear_record(str(record.get("run_id") or ""))
    return _stale_outcome(
        True, "restored the animation scales an earlier run left off", runs
    )


# The same three fail-closed conditions as ``ime_session.restore_stale``: the lock
# probe answered, the device is free or ours, and every record's writer is provably
# dead or has ended its run (``restore`` marks a record it could not put back).
# Restore first, clear only on success, so a device that drops off adb mid-restore
# is retried by the next run.
async def restore_stale(serial: str, *, skip_run_id: str = "") -> dict:
    """Give back animation scales a crashed run left off on *serial*."""
    try:
        found = stale_records(serial, skip_run_id=skip_run_id)
        if not found:
            return _stale_outcome(False, "", [])
        blocked = _stale_blockers(serial, skip_run_id, found)
        if blocked is not None:
            return _stale_outcome(False, "", blocked)
        return await _give_back_oldest(serial, found)
    except Exception as exc:
        logger.exception("mobile.animations.restore_stale failed")
        return {"error": str(exc), "content": None}
