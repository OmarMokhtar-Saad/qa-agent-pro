"""Mobile session explore lane: what EARLIER turns of a run recorded (split from session_explore)."""

from __future__ import annotations

import logging
from typing import Any

from tools.mobile import run_store
from tools.mobile.replay.guard import NOTHING_MOVED_PREFIX
from tools.mobile.session_core import _CASE_STORE_ERRORS

logger = logging.getLogger("tools.mobile.session")


def _turn_int(body: dict) -> int | None:
    """The state's turn number as an int, or None when the field is junk."""
    try:
        return int(body.get("turn") or 0)
    except (TypeError, ValueError, OverflowError):
        return None


# Matched on the turn number rather than taken as the last entry: findings
# are appended only when a turn reports one, so "the last note" would make a
# silent turn inherit the previous turn's finding and report evidence that
# turn never produced.
def _latest_finding(state: object) -> str:
    """The finding recorded for the CURRENT turn, or ``""``."""
    body = state if isinstance(state, dict) else {}
    turn = _turn_int(body)
    if turn is None:
        return ""
    notes = [f for f in list(body.get("findings") or []) if isinstance(f, dict)]
    for entry in reversed(notes):
        try:
            if int(entry.get("turn") or 0) == turn:
                return str(entry.get("note") or "")
        except (TypeError, ValueError, OverflowError):
            continue
    return ""


def _has_assert_pass(listed: dict) -> bool:
    """True when any checkpointed case traces an ``assert_pass`` outcome."""
    for body in listed.get("content") or []:
        if not isinstance(body, dict):
            continue
        for item in list(body.get("trace") or []):
            if (
                isinstance(item, dict)
                and str(item.get("outcome") or "") == "assert_pass"
            ):
                return True
    return False


# The explore lane checkpoints one turn as one case, so the turn that calls
# ``done`` has a trace of its own that can never carry the check an earlier
# turn made. The unit that earns a verdict here is the RUN -- the goal -- so
# the evidence is read run-wide and handed to the executor as an explicit
# input (``executor.Context.prior_verified``). The executor still knows
# nothing about runs, and the scripted lane is untouched because that field
# defaults to False.
#
# The OUTCOME is what counts, not the presence of an assert: a run whose only
# check FAILED has verified nothing.
#
# Never raises: an unreadable case store is "no prior evidence".
def _explore_prior_verified(run_id: str) -> bool:
    """True when an EARLIER turn of this run recorded an ``assert_pass``."""
    try:
        return _has_assert_pass(run_store.list_cases(run_id) or {})
    except _CASE_STORE_ERRORS:  # pragma: no cover - defensive
        logger.warning("mobile.session: could not read this run's prior evidence")
    return False


def _latest_case(listed: dict) -> dict | None:
    """The checkpointed case with the highest turn number in its ``tc_id``."""
    latest: dict | None = None
    latest_turn = -1
    for body in listed.get("content") or []:
        if not isinstance(body, dict):
            continue
        digits = "".join(ch for ch in str(body.get("tc_id") or "") if ch.isdigit())
        turn = int(digits) if digits else -1
        if turn >= latest_turn:
            latest, latest_turn = body, turn
    return latest


def _blocked_trace(case: dict) -> bool:
    """True when a ``type``/``fill``/``clear`` step ended device_error or refused."""
    for item in list(case.get("trace") or []):
        if not isinstance(item, dict):
            continue
        action: Any = item.get("action") if isinstance(item.get("action"), dict) else {}
        if str(action.get("op") or "") in ("type", "fill", "clear") and str(
            item.get("outcome") or ""
        ) in ("device_error", "refused"):
            return True
    return False


# ONE turn, not run-wide like :func:`_explore_prior_verified`: a failure
# several turns back must not block every later pass once the app moved on.
# "Latest" is by the turn number in ``tc_id``, not list position. Counted: a
# reason carrying :data:`executor.NOTHING_MOVED_PREFIX`, or a ``type``/
# ``clear`` whose outcome was ``device_error`` or ``refused``.
#
# Never raises: an unreadable case store is "not blocked".
def _explore_prior_turn_blocked(run_id: str) -> bool:
    """True when the LATEST checkpointed turn failed to type or moved nothing."""
    try:
        latest = _latest_case(run_store.list_cases(run_id) or {})
        if latest is None:
            return False
        if NOTHING_MOVED_PREFIX in str(latest.get("reason") or ""):
            return True
        return _blocked_trace(latest)
    except _CASE_STORE_ERRORS:  # pragma: no cover - defensive
        logger.warning("mobile.session: could not read this run's previous turn")
    return False
