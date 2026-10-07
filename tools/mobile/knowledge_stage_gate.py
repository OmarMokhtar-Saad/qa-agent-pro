"""Learning stage gate (Stage C): promotion, contradiction, decay, and reflection flag."""

from __future__ import annotations

import logging
import sqlite3

from tools.mobile import knowledge_gate
from tools.mobile.knowledge_learn import StageResult

logger = logging.getLogger(__name__)

#: Verified outcomes that make a run worth a reflection.
FAILED_OUTCOMES = frozenset(
    {
        "assert_fail",
        "missing_element",
        "no_change",
        "left_app",
        "route_mismatch",
        "dump_failed",
        "system_dialog",
    }
)


def qualifying(steps: object) -> bool:
    """True when *steps* hold a failed or tester-corrected step."""
    for step in steps or []:
        if not isinstance(step, dict):
            continue
        if step.get("outcome") in FAILED_OUTCOMES or step.get("tester_correction"):
            return True
    return False


def run(conn, lc) -> StageResult:
    notes: list = []
    try:
        moved = knowledge_gate.gate(conn, lc.package, lc.run_id, lc.steps)
        notes = ["%s:%s %s->%s" % (t.table, t.row_id, t.old, t.new) for t in moved[:20]]
        if qualifying(lc.steps):
            conn.execute(
                "UPDATE runs SET reflect_state = 'pending' WHERE run_id = ? AND reflect_state = 'none'",
                (lc.run_id,),
            )
            notes.append("reflection pending")
        return StageResult("gate", wrote=len(moved), notes=notes)
    except (sqlite3.Error, ValueError, TypeError, AttributeError, KeyError):
        logger.exception("knowledge stage gate failed")
        return StageResult("gate", notes=notes, error="gate failed")
