"""Turning the accessibility service on for a run, verifying parity, registering the provider."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import NamedTuple

from tools.mobile import adb, ime_nonce
from tools.mobile.a11y_device import (
    _VALUE_RE,
    MASTER,
    SETTING,
    _items,
    _sh,
    append_service,
    component,
    read_state,
)
from tools.mobile.a11y_dump import _FAILS, fetch_dump, provider
from tools.mobile.a11y_parity import compare
from tools.mobile.a11y_record import write_record
from tools.mobile.a11y_restore import _RUNS, restore
from tools.mobile.replay import dump as replay_dump

logger = logging.getLogger("tools.mobile.a11y")

#: Polls waiting for the service to connect after the setting changed.
A11Y_CONNECT_MAX_POLLS = 10
A11Y_CONNECT_POLL_INTERVAL_S = 0.5

#: Refusal codes that mean "not ready yet" while the service connects.
_NOT_READY = ("no_service", "no_window")


class _Turn(NamedTuple):
    serial: str
    run_id: str
    services: str
    master: str
    ours: str


async def sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


async def _await_connected(serial: str) -> dict:
    last: dict = {"error": "the a11y service did not connect", "content": None}
    for poll in range(A11Y_CONNECT_MAX_POLLS):
        got = await fetch_dump(serial)
        if not got.get("error"):
            return got
        last = got
        if not any(code in str(got["error"]) for code in _NOT_READY):
            return got
        if poll < A11Y_CONNECT_MAX_POLLS - 1:
            await sleep(A11Y_CONNECT_POLL_INTERVAL_S)
    return last


def _put_command(written: str) -> str:
    return (
        "settings put secure "
        + SETTING
        + " '"
        + written
        + "' && settings put secure "
        + MASTER
        + " 1"
    )


def _record_body(turn: _Turn, written: str) -> dict:
    return {
        "serial": str(turn.serial),
        "previous_services": turn.services,
        "previous_master": turn.master,
        "written": written,
        "pid": os.getpid(),
        "recorded_at": time.time(),
    }


async def _turn_on(turn: _Turn) -> dict | None:
    """Record the previous settings and write ours; an error dict, or None on success."""
    if turn.services and not _VALUE_RE.fullmatch(turn.services):
        return {
            "error": "the enabled accessibility services value cannot be restored safely",
            "content": None,
        }
    written = append_service(_items(turn.services), turn.ours)
    recorded = write_record(turn.run_id, _record_body(turn, written))
    if recorded.get("error"):
        return recorded
    put = await _sh(turn.serial, _put_command(written))
    if put.get("error"):
        await restore(turn.run_id)
        return put
    return None


async def _verify_parity(serial: str, run_id: str) -> dict:
    """Wait for the service, compare its dump with uiautomator's; restore on any error."""
    connected = await _await_connected(serial)
    if connected.get("error"):
        await restore(run_id)
        return connected

    slow = await replay_dump.dump_raw(serial, use_provider=False)
    checked = (
        slow
        if slow.get("error")
        else compare(str(connected["content"]), str(slow["content"]))
    )
    if checked.get("error"):
        await restore(run_id)
    return checked


def _unsupported(serial: str) -> dict | None:
    """The silent no-op for a legacy APK (no nonce, protocol < 2), else None."""
    if ime_nonce.protocol(serial) < ime_nonce.PROTOCOL_NONCE or not ime_nonce.get(
        serial
    ):
        return {"error": None, "content": {"enabled": False, "reason": "legacy"}}
    return None


async def _precheck(serial: str, run_id: str):
    """An early result dict, or the ``_Turn`` to apply."""
    early = _unsupported(serial)
    if early:
        return early
    ours = component()
    if not ours:
        return {"error": "no accessibility component is pinned", "content": None}
    state = await read_state(serial)
    if state.get("error"):
        return state
    services, master = state["content"]
    return _Turn(
        serial=serial, run_id=run_id, services=services, master=master, ours=ours
    )


async def _verified(serial: str, run_id: str) -> dict | None:
    _RUNS[str(run_id)] = str(serial)
    checked = await _verify_parity(serial, run_id)
    if checked.get("error"):
        return checked
    _FAILS[str(serial)] = 0
    adb.set_dump_provider(serial, provider)
    return None


# A legacy APK (no nonce, protocol < 2) is a silent no-op. Any failure restores what was
# changed and returns an error; the caller falls back to uiautomator.
async def enable(serial: str, run_id: str) -> dict:
    """Turn the service on for *run_id*, verify parity, register the provider."""
    try:
        turn = await _precheck(serial, run_id)
        if isinstance(turn, dict):
            return turn
        already = turn.ours in _items(turn.services)
        failed = None if already else await _turn_on(turn)
        if failed:
            return failed
        checked = await _verified(serial, run_id)
        if checked:
            return checked
        return {"error": None, "content": {"enabled": True, "changed": not already}}
    except Exception as exc:
        logger.exception("mobile.a11y.enable failed")
        return {"error": str(exc), "content": None}
