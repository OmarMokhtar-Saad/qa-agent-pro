"""System animations turned off for a run, and handed back afterwards.

**Why.** A window, transition or animator scale above zero makes every screen
change take a few hundred milliseconds to settle, and the lane pays that on
every step: the dump that follows an action either waits for the animation or
reads a screen caught halfway through it. Scale 0 is what Espresso and UI
Automator both ask a tester to set by hand. This module sets it for the run
and puts the tester's own values back when the run ends.

**Modelled on ``ime_session``, and deliberately the same shape.** A per-run
record written BEFORE anything on the device changes, a restore that reads only
that record, and a stale sweep that gives back what a crashed run left behind
under the same fail-closed conditions the keyboard sweep uses. The pid check is
``ime_session.pid_is_provably_dead`` itself, not a copy, so the two sweeps
cannot drift apart on what "provably gone" means.

Every public function returns ``{"error", "content"}`` rather than raising.
"""

from __future__ import annotations

import logging
import os
import time

from tools.mobile.animations_record import (  # noqa: F401
    RECORD_FILE,
    clear_record,
    read_record,
    write_record,
)
from tools.mobile.animations_shell import (  # noqa: F401
    KEYS,
    _is_zero,
    _restore_command,
    _run,
    read_scales,
)
from tools.mobile.animations_stale import restore_stale, stale_records  # noqa: F401

logger = logging.getLogger(__name__)


async def _remember(serial: str, run_id: str, previous: dict) -> dict:
    # THE RECORD FIRST. A run that dies between the put and a later write
    # would leave animations off with nothing on disk saying what they were.
    written = write_record(
        run_id,
        {
            "serial": str(serial),
            "previous": previous,
            "pid": os.getpid(),
            "recorded_at": time.time(),
        },
    )
    if written.get("error"):
        return written
    return await _run(
        serial, " && ".join("settings put global " + key + " 0" for key in KEYS)
    )


# Already all 0 is a no-op that writes NO record: there is nothing to give
# back, and a record would make the stale sweep treat a tester's own choice
# as something a crashed run left behind.
async def disable(serial: str, run_id: str) -> dict:
    """Set all three scales to 0 for *run_id*, remembering what they were."""
    try:
        read = await read_scales(serial)
        if read.get("error"):
            return read
        previous = read["content"]
        if all(_is_zero(previous[key]) for key in KEYS):
            return {"error": None, "content": {"changed": False, "previous": previous}}
        result = await _remember(serial, run_id, previous)
        if result.get("error"):
            return result
        return {"error": None, "content": {"changed": True, "previous": previous}}
    except Exception as exc:
        logger.exception("mobile.animations.disable failed")
        return {"error": str(exc), "content": None}


async def _restore_record(run_id: str, record: dict) -> dict:
    serial = str(record.get("serial") or "")
    if not serial:
        clear_record(run_id)
        return {
            "error": None,
            "content": {
                "restored": False,
                "detail": "the run's animation record named no device",
            },
        }
    result = await _run(serial, _restore_command(record.get("previous") or {}))
    if result.get("error"):
        write_record(run_id, {**record, "ended": True})
        return result
    clear_record(run_id)
    return {
        "error": None,
        "content": {"restored": True, "detail": "animation scales restored"},
    }


async def restore(run_id: str) -> dict:
    """Put the run's recorded scales back. Takes NO serial: it reads the record.

    A restore that fails keeps the record, marked ``ended``, so the next run's
    sweep retries it without waiting for this process to die: a device dropped
    off adb at teardown would otherwise stay at scale 0 with nothing to say so.
    """
    try:
        record = read_record(run_id)
        if not record:
            return {"error": None, "content": {"restored": False, "detail": ""}}
        return await _restore_record(run_id, record)
    except Exception as exc:
        logger.exception("mobile.animations.restore failed")
        return {"error": str(exc), "content": None}
