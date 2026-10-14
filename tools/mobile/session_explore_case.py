"""Mobile session explore lane: one turn checkpointed as a case (split from session_explore)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from config.settings_guard import never_raise
from tools.mobile import case_runner, run_store
from tools.mobile.replay.guard import MAX_GUARD_NODE_CHARS
from tools.mobile.session_explore_ids import (
    EXPLORE_TITLE_PREFIX,
    explore_turn_number,
    explore_turn_tc_id,
)

logger = logging.getLogger("tools.mobile.session")


@dataclass(frozen=True)
class TurnRecord:
    """One explore turn's checkpoint input: its state, outcome and what the caller adds."""

    state: object
    outcome: object
    finding: str = ""
    verdict: str = ""
    network: object = None


def _turn_began(body: dict, now: float) -> float:
    """When this turn's packet was built, or *now* when the record has no start."""
    # The window this case's evidence is joined on is ``[started, updated]``.
    # ``started`` is when the PACKET was built, not when this record is written,
    # or the window is the pad around a single instant at the end of the turn
    # and everything the app did during it falls outside.
    began = body.get("turn_started")
    if not isinstance(began, (int, float)) or isinstance(began, bool) or began <= 0:
        return now
    return began


def _explore_result_fields(result: dict, verdict: str) -> dict:
    """The verdict, status, reason and trace of one turn's replay result."""
    return {
        "verdict": str(verdict or ""),
        "status": str(result.get("status") or ""),
        "reason": str(result.get("reason") or "")[:1200],
        "trace": list(result.get("trace") or []),
    }


def _explore_guard_stop(result: dict) -> dict:
    """The guard fingerprint a later explore-lane confirm binds to."""
    # Same fingerprint shape `case_runner._checkpoint` writes, from the
    # SAME per-submit `result` this turn's own replay returned -- so an
    # explore-lane confirm binds to the op THIS run's last turn stopped
    # on, exactly like the scripted lane's `_consume_confirm`. Not
    # carried forward from a prior turn's checkpoint, for the same
    # reason `case_runner._checkpoint` does not carry it forward either.
    return {
        "term": str(result.get("guard_term") or "")[:80],
        "op": str(result.get("guard_op") or "")[:40],
        "node": str(result.get("guard_node") or "")[:MAX_GUARD_NODE_CHARS],
    }


def _explore_turn_evidence(network: object) -> dict:
    """An evidence record shaped like ``case_runner._evidence_record``'s output."""
    # Shaped like `case_runner._evidence_record`'s output so the report's
    # evidence join reads a record rather than a missing key.
    return {
        "profile": None,
        "clock_offset_ms": None,
        "pid": None,
        "skipped": "an exploratory turn takes no app-log slice",
        "slices": 0,
        "slices_written": [],
        # The app LOG is not captured for an exploratory turn; the WIRE is.
        # Two different questions, so two keys and two answers -- the
        # skipped line above is about the log alone.
        "network": case_runner.network_record(network),
    }


def _case_body(record: TurnRecord, now: float) -> dict:
    """The case-shaped dict one exploratory turn is checkpointed as."""
    body = record.state if isinstance(record.state, dict) else {}
    result = record.outcome if isinstance(record.outcome, dict) else {}
    turn = explore_turn_number(body)
    goal = " ".join(str(body.get("goal") or "").split())[:200] or "no goal recorded"
    return {
        "tc_id": explore_turn_tc_id(turn),
        "title": (EXPLORE_TITLE_PREFIX + str(turn) + " — " + goal)[:250],
        **_explore_result_fields(result, record.verdict),
        "escapes": 0,
        "guard_stop": _explore_guard_stop(result),
        "finding": " ".join(str(record.finding or "").split())[:600],
        "started": min(_turn_began(body, now), now),
        "updated": now,
        "evidence": _explore_turn_evidence(record.network),
    }


# Best-effort exactly like ``run_store.write_screen``: a lost checkpoint may
# never lose a turn's replay, so nothing here can return an error into the
# submit path. Returns the body it wrote (or tried to), because the caller
# reports the same id and title back to the tester.
#
# The record carries a STATUS and, on every turn but one, no verdict. An
# exploratory turn has no expected result, so it has none to earn, and
# ``report._verdict_of`` shows the status instead -- never inventing a pass.
# The exception is a turn whose ``done()`` judged the goal: the unit that
# earns a verdict in this lane is the RUN, and the caller passes that verdict
# in so the case-driven report has one to render, in the same field the suite
# lane uses.
def _checkpoint_explore_turn(run_id: str, record: TurnRecord) -> dict:
    """Write ONE exploratory turn as a case-shaped record. Never raises."""
    written = _case_body(record, time.time())
    with never_raise(
        logger,
        "mobile.session: could not checkpoint an explore turn",
        level=logging.WARNING,
    ):
        run_store.write_case(run_id, written["tc_id"], written)
    return written
