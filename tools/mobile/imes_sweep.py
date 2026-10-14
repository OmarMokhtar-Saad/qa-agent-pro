"""Find earlier runs' keyboard records and prove their holders are gone."""

from __future__ import annotations

import logging
import os
from typing import Any, NamedTuple

from tools.mobile import imes_record, run_records
from tools.mobile import locks as mobile_locks

logger = logging.getLogger("tools.mobile.ime_session")

#: How many recent runs the stale-keyboard scan reads.
#:
#: This runs on the critical path of TAKING a device, and each run costs one
#: small JSON read. The constraint is that a tester waits for it before their
#: first screen: a crashed run is the newest thing on disk after the crash, so
#: the answer is always near the top of a newest-first listing, and reading
#: further buys nothing a tester would wait for.
IME_STALE_SCAN_RUNS = 40


class _Sweep(NamedTuple):
    """The fields of one ``restore_stale`` result body."""

    restored: bool
    detail: str
    runs: list
    previous: str = ""


def _sweep_result(body: _Sweep) -> dict:
    """The ``restore_stale`` result body."""
    return {
        "error": None,
        "content": {
            "restored": body.restored,
            "detail": body.detail,
            "previous": body.previous,
            "runs": body.runs,
        },
    }


def _as_pid(pid: Any) -> int | None:
    """*pid* as a positive int, or ``None`` when it is not a usable pid."""
    try:
        number = int(pid)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number > 0 else None


def _is_gone(number: int) -> bool:
    """True only when signal 0 reports that no such process exists."""
    try:
        os.kill(number, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        # A process we may not signal is still a process.
        return False
    except (OSError, OverflowError):
        return False
    return False


# True only when *pid* is a real pid this process can show is GONE.
#
# Every uncertain answer is False, and the asymmetry is the point. This
# decides whether to change a device belonging to somebody else's run, so "I
# could not tell" must read as "leave it alone". A pid of 0, a pid this user
# may not signal, an OS that will not say, a value that is not a number at
# all: all are "not proven dead", none is "dead".
#
# NOT ``mobile_capture.teardown._pid_alive``. That answers "should I try to
# stop this process" and resolves its unknowns to False, which is right for a
# caller about to send a signal and exactly wrong here -- it would report an
# un-signallable live run as gone. Same subject, opposite safe default, so it
# is its own function rather than a caller of that one.
def pid_is_provably_dead(pid: Any) -> bool:
    """True only when *pid* is a real pid this process can show is GONE."""
    number = _as_pid(pid)
    return number is not None and _is_gone(number)


def stale_records(serial: str, *, skip_run_id: str = "") -> list:
    """Records from OTHER runs that still claim to have changed *serial*.

    A record exists only between `ensure_ready` and `restore`, so one left on
    disk means a run ended without running its restore -- a killed editor, a
    crashed process. `restore_stale` decides what to do about it; this only
    finds them, so the decision has one place and the search has another.
    """
    return run_records.stale_records(
        run_records.StaleScan(
            serial,
            skip_run_id,
            IME_STALE_SCAN_RUNS,
            imes_record.read_record,
            logger,
            "mobile.ime_session",
        )
    )


# THREE CONDITIONS, ALL REQUIRED, ALL FAILING CLOSED. This changes a
# device belonging to somebody else's run, so the bar is not "looks
# idle" but "is provably not in use".
#
# (a) THE PROBE ANSWERED. No row, or a row carrying an error, is not
#     evidence of an idle device.
# (b) THE DEVICE IS FREE OR OURS. Asking only "is it held" was the
#     defect a review caught: both call sites run while THIS run holds
#     the lock -- `_mobile_hand_off` takes it and then sweeps,
#     `finish_device` sweeps before releasing -- so the hold the guard
#     tripped over was its own and the sweep could never fire.
#     Measured before the fix: held=True, owner=our own run id.
# (c) EVERY WRITER IS PROVABLY GONE. A lock can be free while its
#     writer lives, so this is the condition that actually protects a
#     live run; (b) is the weaker check, not the only one.
def _probe_unclear(serial: str, skip_run_id: str) -> bool:
    """True when the lock probe did not answer "free or ours" (a, b)."""
    rows = mobile_locks.device_holders([str(serial)]) or []
    row = rows[0] if rows else {}
    mine = str(skip_run_id or "")
    held_by_another = bool(row.get("held")) and str(row.get("owner") or "") != mine
    return bool(not rows or row.get("error") or held_by_another)


def _unconfirmed_holders(serial: str, skip_run_id: str, found: list) -> list | None:
    """``None`` when the device is provably idle; else the run ids not proven dead.

    An empty list still means "refuse": the probe itself gave no clear answer.
    """
    unclear = _probe_unclear(serial, skip_run_id)
    undead = [r["run_id"] for r in found if not pid_is_provably_dead(r.get("pid"))]
    if unclear or undead:
        return undead
    return None


def _earliest_previous(found: list) -> str:
    """The keyboard to give back: the oldest record's, never our own."""
    # OLDEST first: the earliest record holds the keyboard the tester
    # actually chose. A later crashed run may have recorded OUR keyboard as
    # its previous, and restoring that would hand back the thing being
    # undone.
    found.sort(key=lambda r: float(r.get("selected_at") or 0.0))
    for record in found:
        candidate = str(record.get("previous") or "")
        if candidate and not record.get("was_ours"):
            return candidate
    return ""


def _clear_all(found: list) -> None:
    for record in found:
        imes_record.clear_record(str(record.get("run_id") or ""))
