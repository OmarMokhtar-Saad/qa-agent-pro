"""The sweep that gives back accessibility values a crashed run left on a device."""

from __future__ import annotations

import logging

from tools.mobile import ime_session, run_records
from tools.mobile import locks as mobile_locks
from tools.mobile.a11y_record import clear_record, read_record
from tools.mobile.a11y_restore import _give_back

logger = logging.getLogger("tools.mobile.a11y")


def stale_records(serial: str, *, skip_run_id: str = "") -> list:
    """Records from OTHER runs that still claim to have changed *serial*."""
    return run_records.stale_records(
        run_records.StaleScan(
            serial,
            skip_run_id,
            ime_session.IME_STALE_SCAN_RUNS,
            read_record,
            logger,
            "mobile.a11y",
        )
    )


def _stale_outcome(restored: bool, detail: str, runs: list) -> dict:
    return {
        "error": None,
        "content": {"restored": restored, "detail": detail, "runs": runs},
    }


def _undead(found: list) -> list:
    return [
        r["run_id"]
        for r in found
        if r.get("ended") is not True
        and not ime_session.pid_is_provably_dead(r.get("pid"))
    ]


def _stale_blockers(serial: str, skip_run_id: str, found: list) -> list | None:
    """Run ids that stop a restore, or None when it is safe to go ahead."""

    rows = mobile_locks.device_holders([str(serial)]) or []
    row = rows[0] if rows else {}
    mine = str(skip_run_id or "")
    held_by_another = bool(row.get("held")) and str(row.get("owner") or "") != mine
    undead = _undead(found)
    if not rows or row.get("error") or held_by_another or undead:
        return undead or [r["run_id"] for r in found]
    return None


def _failed_detail(result: dict) -> str:
    return (
        "could not restore the accessibility services an earlier run left on ("
        + str(result["error"])[:160]
        + "). It will be tried again next time."
    )


async def _give_back_oldest(serial: str, found: list) -> dict:
    runs = [r["run_id"] for r in found]
    found.sort(key=lambda r: float(r.get("recorded_at") or 0.0))
    result = await _give_back(serial, found[0])
    if result.get("error"):
        return _stale_outcome(False, _failed_detail(result), runs)
    for record in found:
        clear_record(str(record.get("run_id") or ""))
    return _stale_outcome(
        True, "restored the accessibility services an earlier run left on", runs
    )


# Fail-closed, as animations: restore first, clear only on success.
async def restore_stale(serial: str, *, skip_run_id: str = "") -> dict:
    """Give back accessibility values a crashed run left on *serial*."""
    try:
        found = stale_records(serial, skip_run_id=skip_run_id)
        if not found:
            return _stale_outcome(False, "", [])
        blocked = _stale_blockers(serial, skip_run_id, found)
        if blocked is not None:
            return _stale_outcome(False, "", blocked)
        return await _give_back_oldest(serial, found)
    except Exception as exc:
        logger.exception("mobile.a11y.restore_stale failed")
        return {"error": str(exc), "content": None}
