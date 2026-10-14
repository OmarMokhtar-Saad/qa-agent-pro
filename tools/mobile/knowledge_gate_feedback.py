"""Stage C step 1: the feedback of one run (disputes, contradiction counters, variants)."""

from __future__ import annotations

import json
import logging
from typing import Any

from tools.mobile import knowledge_db as kdb
from tools.mobile import knowledge_feedback
from tools.mobile.knowledge_gate_core import (
    ERRORS,
    KEEP_OUT,
    TEXT_COLUMN,
    Case,
    Transition,
    Variant,
    is_open,
    row_of,
)

logger = logging.getLogger(__name__)


def _variant_values(table: str, old: Any, variant: Variant) -> dict:
    """The new row for a context-bound proposal: the old row's keys, the new text."""
    text, env = variant.text, variant.env
    values = {k: old[k] for k in old.keys() if k not in KEEP_OUT}
    column = TEXT_COLUMN.get(table)
    if column:
        values[column] = text
    if table == "notes":
        when = json.loads(old["when_json"] or "{}")
        when["env"] = {**(when.get("env") or {}), **env}
        values["when_json"] = json.dumps(when, sort_keys=True)
        values["scope_key"] = "%s|%s" % (old["kind"], json.dumps(when, sort_keys=True))
        values["variant_of"] = old["id"]
    elif table == "lessons":
        values["text"] = "(%s) %s" % (
            ", ".join("%s=%s" % kv for kv in env.items()),
            text,
        )
    return values


def _insert_variant(conn: Any, case: Case) -> int | None:
    fb, table, old = case.fb, case.table, case.row
    env = knowledge_feedback.context_of(fb.get("observation"))
    text = str(fb.get("proposed") or "").strip()
    if not env or not text or table not in TEXT_COLUMN:
        return None
    values = _variant_values(table, old, Variant(text, env))
    values.update(status="candidate", trust="host")
    write = kdb.Write(values, run_id=case.run_id, why="variant of %s" % old["id"])
    return kdb.insert(conn, table, write)


def _proposal(case: Case) -> str:
    fb = case.fb
    return json.dumps(
        {
            "proposed": fb.get("proposed", ""),
            "observation": fb.get("observation", ""),
            "env": knowledge_feedback.context_of(fb.get("observation")),
            "run_id": case.run_id,
        },
        sort_keys=True,
    )


def _dispute_tester_note(conn: Any, case: Case) -> Transition | None:
    """A tester note is never auto-changed. An avoid note keeps guarding (it stays
    active, flagged by its proposal); any other kind becomes ``disputed``."""
    row, proposal = case.row, _proposal(case)
    status = row["status"] if row["kind"] == "avoid" else "disputed"
    if row["status"] == status and row["proposal_json"] == proposal:
        return None
    move = kdb.Status(
        row["id"],
        status,
        {"proposal_json": proposal},
        run_id=case.run_id,
        why="disputed",
    )
    if kdb.set_status(conn, "notes", move):
        return Transition(
            "notes", row["id"], row["status"], status, "tester note disputed"
        )
    return None


def _bump(conn: Any, case: Case, delta: int) -> Transition | None:
    row = case.row
    fields = {
        "runs_json": row["runs_json"] or "[]",
        "contradicted": int(row["contradicted"] or 0) + delta,
    }
    move = kdb.Status(
        row["id"], row["status"], fields, run_id=case.run_id, why="feedback"
    )
    if not kdb.set_status(conn, case.table, move):
        return None
    return Transition(
        case.table, row["id"], row["status"], row["status"], "contradicted"
    )


def _count_contradiction(conn: Any, case: Case) -> Transition | None:
    """A learned item: evidence counters (a half-weight report needs a second one)."""
    row = case.row
    if _insert_variant(conn, case) is not None:
        return Transition(
            case.table, row["id"], row["status"], row["status"], "variant added"
        )
    delta = 1 if float(case.fb.get("weight") or 0) >= 1.0 else 0
    if delta <= 0:
        return None
    return _bump(conn, case, delta)


def _one_feedback(conn: Any, fb: dict, run_id: str) -> Transition | None:
    parsed = kdb.parse_item_id(fb.get("item_id"))
    if parsed is None:
        return None
    table, row_id = parsed
    row = row_of(conn, table, row_id)
    if not is_open(row):
        return None
    case = Case(table, row, fb, run_id)
    try:
        if table == "notes" and row["trust"] == "tester":
            return _dispute_tester_note(conn, case)
        return _count_contradiction(conn, case)
    except ERRORS:
        logger.exception("knowledge gate: feedback on %s failed", fb.get("item_id"))
        return None


def _feedback(conn: Any, run_id: str) -> list:
    done: list = []
    for fb in knowledge_feedback.run_feedback(conn, run_id):
        got = _one_feedback(conn, fb, run_id)
        if got:
            done.append(got)
    return done
