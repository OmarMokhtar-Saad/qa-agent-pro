"""Lease building blocks: the path, the read, and the take-over decision.

:func:`_swap_lease` writes the lease, re-reads it, and returns the session now
on disk.

Compare-after-swap. See :func:`tools.mobile.run_lease.acquire_lease`: this
narrows the read-decide-write race rather than closing it, and saying so is the
point -- a caller told `acquired=True` for a lease somebody else now holds
would go on producing packets for a device it does not own.

:func:`_lease_decision` returns ``(reason, taken_over_from)``;
``held_by_other`` carries the holder instead. :func:`_lease_answer` is the
``acquire_lease`` reply envelope, and :func:`_lease_input_error` says why
``acquire_lease`` refuses these ids, or ``None``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from tools.mobile import run_consts, run_io

logger = logging.getLogger("tools.mobile.run_store")


class _Claim(NamedTuple):
    run_id: str
    session_id: str
    moment: float
    force: bool


@dataclass(frozen=True)
class LeaseOpts:
    """Optional inputs of ``acquire_lease``."""

    now: float | None = None
    force: bool = False


def _lease_path(run_id: str) -> Path:
    return run_io.run_path(run_id) / run_consts.LEASE_FILE


def read_lease(run_id: str) -> dict:
    try:
        if not run_io.valid_run_id(run_id):
            return {"error": "Invalid run id.", "content": None}
        return {"error": None, "content": run_io._read_json(_lease_path(run_id))}
    except Exception as exc:
        logger.exception("mobile.run_store.read_lease failed")
        return {"error": str(exc), "content": None}


# The ``acquire_lease`` reply envelope.
def _lease_answer(acquired: bool, holder: str, why: tuple[str, str]) -> dict:
    """The ``acquire_lease`` reply envelope; *why* is ``(taken_from, reason)``."""
    return {
        "error": None,
        "content": {
            "acquired": acquired,
            "holder": holder,
            "taken_over_from": why[0],
            "reason": why[1],
        },
    }


def _lease_decision(current: dict | None, claim: _Claim) -> tuple[str, str]:
    """``(reason, taken_over_from)``; ``held_by_other`` carries the holder instead."""
    holder = str((current or {}).get("session_id") or "")
    heartbeat = float((current or {}).get("heartbeat") or 0)
    if current and holder and holder != claim.session_id:
        stale = (claim.moment - heartbeat) > run_consts.LEASE_STALE_S
        if not (stale or claim.force):
            return "held_by_other", holder
        return ("stale" if stale else "forced"), holder
    if current and holder == claim.session_id:
        return "refresh", str(current.get("taken_over_from") or "")
    return "new", ""


def _lease_input_error(run_id: str, session_id: object) -> str | None:
    """Why :func:`acquire_lease` refuses these ids, or ``None``."""
    if not run_io.valid_run_id(run_id):
        return "Invalid run id."
    if not (isinstance(session_id, str) and run_consts._SESSION_RE.match(session_id)):
        return "Refusing " + repr(str(session_id)[:40]) + " as a session id."
    return None


# Compare-after-swap. See :func:`acquire_lease`: this narrows the read-decide-
# write race rather than closing it, and saying so is the point -- a
# caller told `acquired=True` for a lease somebody else now holds would
# go on producing packets for a device it does not own.
def _swap_lease(claim: _Claim, current: dict | None, decision: tuple[str, str]) -> str:
    """Write the lease, re-read it, and return the session now on disk."""
    reason, taken_from = decision
    moment = claim.moment
    payload = {
        "session_id": claim.session_id,
        "heartbeat": moment,
        "acquired": moment
        if reason != "refresh"
        else (current or {}).get("acquired", moment),
        "taken_over_from": taken_from,
        "pid": os.getpid(),
    }
    run_io._write_json(_lease_path(claim.run_id), payload)
    confirmed = run_io._read_json(_lease_path(claim.run_id))
    return str((confirmed or {}).get("session_id") or "")
