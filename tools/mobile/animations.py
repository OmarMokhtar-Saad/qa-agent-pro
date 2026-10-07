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
import re
import time

from tools.mobile import adb, ime_session, run_store

logger = logging.getLogger(__name__)

#: The per-run record of the animation scales this run displaced.
RECORD_FILE = "animations.json"

#: The three global settings Android's Developer options call "Window animation
#: scale", "Transition animation scale" and "Animator duration scale".
KEYS = (
    "window_animation_scale",
    "transition_animation_scale",
    "animator_duration_scale",
)

#: A value this module will WRITE back with ``settings put``. The previous values
#: are device output, so they are untrusted: anything that is not a plain number
#: is restored with ``settings delete`` (the Android default) instead of being
#: pasted into a shell command line.
_SCALE_RE = re.compile(r"\d{1,3}(\.\d{1,6})?")


def _record_path(run_id: str):
    return run_store.run_path(str(run_id)) / RECORD_FILE


def read_record(run_id: str) -> dict:
    """The run's animation record, or ``{}``. Never raises."""
    try:
        body = run_store._read_json(_record_path(run_id))
    except Exception:  # pragma: no cover - _read_json already swallows
        logger.exception("mobile.animations.read_record failed")
        return {}
    return body if isinstance(body, dict) else {}


def write_record(run_id: str, body: dict) -> dict:
    """Write the run's animation record through run_store's own writer."""
    try:
        run_store._write_json(_record_path(run_id), dict(body or {}))
        return {"error": None, "content": dict(body or {})}
    except Exception as exc:
        logger.exception("mobile.animations.write_record failed")
        return {"error": str(exc), "content": None}


def clear_record(run_id: str) -> None:
    """Drop the record. A missing file is success: the point is that it is gone."""
    try:
        target = _record_path(run_id)
        if target.is_file():
            target.unlink()
    except Exception:
        logger.exception("mobile.animations.clear_record failed")


def _is_zero(value: str) -> bool:
    return bool(_SCALE_RE.match(value)) and float(value) == 0.0


async def _run(serial: str, command: str) -> dict:
    """One ``adb shell`` round trip; a non-zero exit is an error, not success."""
    result = await adb.shell(str(serial), [command])
    if result.get("error"):
        return result
    body = result.get("content") or {}
    if int(body.get("rc") or 0) != 0:
        detail = (
            str(body.get("err") or "").strip() or str(body.get("out") or "").strip()
        )
        return {
            "error": "adb shell settings failed ("
            + (detail[:200] if detail else "exit " + str(body.get("rc")))
            + ")",
            "content": None,
        }
    return {"error": None, "content": str(body.get("out") or "")}


async def read_scales(serial: str) -> dict:
    """``{key: value}`` for the three scales, in ONE shell call. Never raises."""
    try:
        result = await _run(
            serial, "; ".join("settings get global " + key for key in KEYS)
        )
        if result.get("error"):
            return result
        lines = [line.strip() for line in str(result["content"]).splitlines()]
        lines = [line for line in lines if line]
        if len(lines) != len(KEYS):
            return {
                "error": "unexpected animation settings output ("
                + str(len(lines))
                + " lines for "
                + str(len(KEYS))
                + " keys)",
                "content": None,
            }
        return {"error": None, "content": dict(zip(KEYS, (v[:40] for v in lines)))}
    except Exception as exc:
        logger.exception("mobile.animations.read_scales failed")
        return {"error": str(exc), "content": None}


def _restore_command(previous: dict) -> str:
    parts = []
    for key in KEYS:
        value = str((previous or {}).get(key) or "")
        if _SCALE_RE.fullmatch(value):
            parts.append("settings put global " + key + " " + value)
        else:
            parts.append("settings delete global " + key)
    return " && ".join(parts)


async def disable(serial: str, run_id: str) -> dict:
    """Set all three scales to 0 for *run_id*, remembering what they were.

    Already all 0 is a no-op that writes NO record: there is nothing to give
    back, and a record would make the stale sweep treat a tester's own choice
    as something a crashed run left behind.
    """
    try:
        read = await read_scales(serial)
        if read.get("error"):
            return read
        previous = read["content"]
        if all(_is_zero(previous[key]) for key in KEYS):
            return {"error": None, "content": {"changed": False, "previous": previous}}
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
        result = await _run(
            serial, " && ".join("settings put global " + key + " 0" for key in KEYS)
        )
        if result.get("error"):
            return result
        return {"error": None, "content": {"changed": True, "previous": previous}}
    except Exception as exc:
        logger.exception("mobile.animations.disable failed")
        return {"error": str(exc), "content": None}


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
    except Exception as exc:
        logger.exception("mobile.animations.restore failed")
        return {"error": str(exc), "content": None}


def stale_records(serial: str, *, skip_run_id: str = "") -> list:
    """Records from OTHER runs that still claim to have changed *serial*."""
    out: list = []
    try:
        wanted = str(serial or "")
        if not wanted:
            return out
        listed = run_store.list_runs(ime_session.IME_STALE_SCAN_RUNS, gc=False) or {}
        for row in listed.get("content") or []:
            run_id = str((row or {}).get("run_id") or "")
            if not run_id or run_id == str(skip_run_id or ""):
                continue
            record = read_record(run_id)
            if not record or str(record.get("serial") or "") != wanted:
                continue
            out.append({"run_id": run_id, **record})
    except Exception:
        logger.exception("mobile.animations.stale_records failed")
    return out


def _stale_outcome(restored: bool, detail: str, runs: list) -> dict:
    return {
        "error": None,
        "content": {"restored": restored, "detail": detail, "runs": runs},
    }


def _stale_blockers(serial: str, skip_run_id: str, found: list) -> list | None:
    """Run ids that stop a restore, or None when it is safe to go ahead."""
    from tools.mobile import locks as mobile_locks

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


async def restore_stale(serial: str, *, skip_run_id: str = "") -> dict:
    """Give back animation scales a crashed run left off on *serial*.

    The same three fail-closed conditions as ``ime_session.restore_stale``: the
    lock probe answered, the device is free or ours, and every record's writer
    is provably dead or has ended its run (``restore`` marks a record it could
    not put back). Restore first, clear only on success, so a device that
    drops off adb mid-restore is retried by the next run.
    """
    try:
        found = stale_records(serial, skip_run_id=skip_run_id)
        if not found:
            return _stale_outcome(False, "", [])
        blocked = _stale_blockers(serial, skip_run_id, found)
        if blocked is not None:
            return _stale_outcome(False, "", blocked)
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
    except Exception as exc:
        logger.exception("mobile.animations.restore_stale failed")
        return {"error": str(exc), "content": None}
