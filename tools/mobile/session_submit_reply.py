"""Mobile session submit: verdict, case block and chat reply of an explore turn (split from session_submit)."""

from __future__ import annotations

import logging

from tools.mobile import explore_runner
from tools.mobile.session_core import STATE_REPORT, STATE_RUNNING
from tools.mobile.session_submit_args import _TurnReply

logger = logging.getLogger("tools.mobile.session")


# Only a turn whose ``done()`` judged the goal writes a verdict; every
# other turn keeps "" plus its status, because an exploratory turn has no
# expected result to be judged against. It lands in the ordinary
# ``verdict`` field, exactly as the suite lane writes it, so
# ``run_store.DONE_VERDICTS`` and every reader of it work unchanged.
#
# The condition is STATED, not inferred from the producer:
# ``executor.replay``'s no-done exit returns ``unverified`` too, so reading
# the field alone recorded a turn that merely acted as one that had reached
# a verdict.
def _turn_verdict(outcome: dict) -> str:
    """The verdict of a turn whose ``done()`` judged the goal, else ""."""
    ended = any(
        str((entry.get("action") or {}).get("op") or "") == "done"
        for entry in list(outcome.get("trace") or [])
        if isinstance(entry, dict)
    )
    return str(outcome.get("verdict") or "") if ended else ""


def _explore_turn_case(checkpoint: dict, outcome: dict, verdict: str) -> dict:
    """The ``case`` block of the chat reply for one replayed turn."""
    return {
        "tc_id": checkpoint["tc_id"],
        "title": checkpoint["title"],
        "status": str(outcome.get("status") or ""),
        "verdict": verdict,
        "reason": str(outcome.get("reason") or ""),
        "trace": list(outcome.get("trace") or []),
    }


def _explore_turn_reply(reply: _TurnReply) -> dict:
    """The chat reply for one folded exploratory turn."""
    return {
        "error": None,
        "content": {
            "state": STATE_REPORT if reply.stopped else STATE_RUNNING,
            "case": reply.case,
            "packet": None,
            "next": {},
            "notice": reply.notice,
            "field": "",
            "resolved": reply.resolved,
        },
    }


# The replay HAPPENED, so the number this turn was handed is now earned.
# This is the only writer that ADVANCES ``committed_turn`` to a number the
# ladder has not already earned, and it
# sits below every path that ends without a replay: the parse refusal above
# returns before ``executor.replay``, a replay error returns before this
# line, and a packet nobody ever answers never reaches this module at all.
# Each of those leaves the ledger where it was, so
# ``explore_runner.next_turn`` re-allocates the SAME number next time and the
# report grows neither a gap nor a duplicate card.
#
# No coercion guard here, and no ``# pragma``. ``apply_turn_result`` above
# calls ``explore_runner.stop_reason``, which coerces this same ``turn``, so
# on every path where ``state["stop"]`` is empty a junk turn number has
# already been answered with a refusal before this line -- a guard here would
# be a branch no test can enter, an ungraded clause wearing a note.
#
# Where ``stop`` is NOT empty, ``stop_reason`` returns before its coercion
# and this line does raise; ``submit``'s own try/except contains it and the
# caller gets an error dict, never an exception. That corner is why the guard
# is deleted rather than corrected: it used to answer with
# ``committed_turn = 0``, which restarts numbering at TC-001 over cards the
# run already wrote.
#
# This turn's capture is finished and its record now lives on the case
# record. Leaving it on the state would have the NEXT turn's begin treat it
# as a capture still running and abort a file that is already gone.
#
# THE REFUSE, RECORDED AS A STOP. The executor marks a charter-driven
# refusal explicitly (``guard_refused``), and a refused run is OVER -- so
# the state says so and every reader under ``stop_reason`` sees it.
# Without this the reply would carry the refusal text and still report a
# running run: a success-shaped message behind a refusal, which is exactly
# what this lane's rule forbids.
def _committed_explore_state(turn: dict, outcome: dict) -> tuple[dict, bool]:
    """The explore state to persist for a replayed turn, and whether it refused."""
    committed_state = dict(turn.get("state") or {})
    committed_state["committed_turn"] = int(committed_state.get("turn") or 0)
    committed_state.pop("network", None)
    refused = bool(outcome.get("guard_refused"))
    if refused:
        committed_state["stop"] = explore_runner.STOP_GUARD_REFUSED
    return committed_state, refused
