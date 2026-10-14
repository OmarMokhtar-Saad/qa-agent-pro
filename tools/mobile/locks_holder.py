"""``holder``: who holds a lock right now, without taking anything.

Split out of :mod:`tools.mobile.locks` (the facade).
"""

from __future__ import annotations

import logging
import os
import time
from typing import NamedTuple

from tools.mobile import locks_body, locks_fd
from tools.mobile.locks_state import _HELD, _MUTEX, EMULATOR_LOCK, _refuse_name

logger = logging.getLogger("tools.mobile.locks")

#: A hold longer than this is DISCLOSED -- logged CRITICAL, surfaced by
#: qa-doctor -- and never broken. A hung holder is a human problem; breaking its
#: lock is the two-holder defect this module exists to delete. A module constant
#: rather than a setting: it tunes a log line, not behaviour.
HELD_TOO_LONG_S = 600


class HolderRow(NamedTuple):
    """The three facts a ``holder`` answer carries besides ``held`` and ``pid``."""

    owner: str
    age: float
    mine: bool


def _holder_result(held: bool, pid: int, row: HolderRow) -> dict:
    """The ``holder`` envelope: one shape for every branch."""
    return {
        "error": None,
        "content": {
            "held": held,
            "pid": pid,
            "owner": row.owner,
            "age": row.age,
            "held_too_long": row.age > HELD_TOO_LONG_S,
            "mine": row.mine,
        },
    }


def _held_here(name: str, moment: float) -> dict | None:
    """The answer when THIS process holds *name*, else ``None``."""
    with _MUTEX:
        mine = _HELD.get(name)
        if mine is None:
            return None
        return _holder_result(
            True, os.getpid(), HolderRow(mine.owner, moment - mine.since, True)
        )


# THE FILE IS IMMORTAL, so its existence proves nothing and its body
# proves only that somebody once stamped it. Whether the lock is held
# RIGHT NOW is a question only the kernel can answer, so ask it: a
# non-blocking probe that immediately releases whatever it took. The
# probe releases only its OWN lock, never anybody else's, so the
# invariant holds. Its cost is a microseconds-wide window in which a
# real contender could be refused -- which is why nothing but
# diagnostics calls this function.
def _held_elsewhere(name: str, moment: float) -> dict:
    """The answer from the lock file and the kernel probe."""
    body = locks_body.read(name) or {}
    if locks_fd.probe_free(name):
        return _holder_result(False, 0, HolderRow("", 0.0, False))
    row = HolderRow(
        str(body.get("owner") or ""), locks_body._body_age(body, moment), False
    )
    return _holder_result(True, int(body.get("pid") or 0), row)


def _holder_checked(name: str, now: float | None) -> dict:
    """``holder`` without the never-raise wrapper."""
    refusal = _refuse_name(name)
    if refusal is not None:
        return refusal
    moment = float(now) if now is not None else time.time()
    local = _held_here(str(name), moment)
    return local if local is not None else _held_elsewhere(name, moment)


# Diagnostics and qa-doctor only. ``stale`` is deliberately GONE: it was the
# age-from-body defect wearing a different hat, and leaving it would let that
# defect come back through a new call path. ``held_too_long`` is a DISCLOSURE,
# never a decision.
def holder(name: str = EMULATOR_LOCK, *, now: float | None = None) -> dict:
    """``{held, pid, owner, age, held_too_long, mine}`` without taking anything."""
    try:
        return _holder_checked(name, now)
    except Exception as exc:
        logger.exception("mobile.locks.holder failed")
        return {"error": str(exc), "content": None}
