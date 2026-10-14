"""Giving the recorded accessibility settings back when a run ends."""

from __future__ import annotations

import logging

from tools.mobile import adb
from tools.mobile.a11y_device import (
    _items,
    _norm,
    _restore_command,
    _sh,
    component,
    read_state,
)
from tools.mobile.a11y_dump import _FAILS
from tools.mobile.a11y_record import clear_record, read_record, write_record

logger = logging.getLogger("tools.mobile.a11y")

_RUNS: dict = {}


def _target_values(record: dict, current: str) -> tuple:
    ours = component()
    if current == _norm(record.get("written")):
        services = _norm(record.get("previous_services"))
        master: str | None = _norm(record.get("previous_master"))
    else:
        services = ":".join(part for part in _items(current) if part != ours)
        # Another service is enabled now: the master switch is theirs to keep on, so it is
        # not put back to the value from before this run (it may have been "0").
        master = None if services else _norm(record.get("previous_master"))
    return services, master


async def _give_back(serial: str, record: dict) -> dict:
    state = await read_state(serial)
    if state.get("error"):
        return state
    services, master = _target_values(record, state["content"][0])
    command = _restore_command(services, master)
    if command is None:
        return {
            "error": "the recorded accessibility value is unsafe to write back",
            "content": None,
        }
    return await _sh(serial, command)


def _restore_target(run_id: str) -> tuple:
    remembered = _RUNS.pop(str(run_id), "")
    record = read_record(run_id)
    serial = str(record.get("serial") or remembered)
    if serial:
        adb.clear_dump_provider(serial)
        _FAILS.pop(serial, None)
    return record, serial


def _no_device(run_id: str) -> dict:
    clear_record(run_id)
    return {
        "error": None,
        "content": {
            "restored": False,
            "detail": "the a11y record named no device",
        },
    }


def _end_record(run_id: str, record: dict, result: dict) -> dict:
    if result.get("error"):
        write_record(run_id, {**record, "ended": True})
        return result
    clear_record(run_id)
    return {
        "error": None,
        "content": {"restored": True, "detail": "accessibility services restored"},
    }


# A failed restore keeps the record, marked ``ended``, so the next run's sweep retries.
async def restore(run_id: str) -> dict:
    """Disable the service and put the recorded values back. Takes NO serial."""
    try:
        record, serial = _restore_target(run_id)
        if not record:
            return {"error": None, "content": {"restored": False, "detail": ""}}
        if not serial:
            return _no_device(run_id)
        return _end_record(run_id, record, await _give_back(serial, record))
    except Exception as exc:
        logger.exception("mobile.a11y.restore failed")
        return {"error": str(exc), "content": None}
