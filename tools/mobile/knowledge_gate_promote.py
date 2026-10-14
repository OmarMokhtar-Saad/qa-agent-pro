"""Stage C steps 2 and 3: promotion, and contradiction beats confirmation."""

from __future__ import annotations

from typing import Any

from tools.mobile import knowledge_db as kdb
from tools.mobile import knowledge_feedback
from tools.mobile import knowledge_limits as limits
from tools.mobile.knowledge_gate_core import (
    KEEP_OUT,
    MIN_RUNS,
    TEXT_COLUMN,
    Case,
    Transition,
    runs_of,
)


def _why(row: Any, table: str, need: int) -> str | None:
    """Why a candidate row becomes active now, or None when it stays a candidate."""
    if row["trust"] == "tester":
        return "tester trust"
    if int(row["contradicted"] or 0) > int(row["confirmed"] or 0):
        return None
    if table == "shortcuts" and int(row["fail"] or 0) > 0:
        return None
    if runs_of(row) >= need:
        return "%d distinct runs" % runs_of(row)
    return None


def _promote(conn: Any, run_id: str, table: str) -> list:
    done: list = []
    need = MIN_RUNS[table]
    rows = conn.execute(
        "SELECT * FROM %s WHERE status = 'candidate' AND invalid_at IS NULL ORDER BY id"
        % table
    ).fetchall()
    for row in rows:
        why = _why(row, table, need)
        if why is None:
            continue
        move = kdb.Status(row["id"], "active", None, run_id=run_id, why=why)
        if kdb.set_status(conn, table, move):
            done.append(Transition(table, row["id"], "candidate", "active", why))
    return done


def _contradicted_rows(conn: Any, table: str) -> list:
    return conn.execute(
        "SELECT * FROM %s WHERE status = 'active' AND invalid_at IS NULL AND trust != 'tester'"
        " AND contradicted >= ? AND contradicted > confirmed ORDER BY id" % table,
        (limits.CONTRADICT_FLOOR,),
    ).fetchall()


def _transition(case: Case, new: str, why: str) -> Transition:
    return Transition(case.table, case.row["id"], "active", new, why)


def _replace(conn: Any, case: Case, proposal: dict) -> Transition | None:
    row, table = case.row, case.table
    values = {k: row[k] for k in row.keys() if k not in KEEP_OUT}
    values.update(
        {
            TEXT_COLUMN[table]: proposal["proposed"],
            "status": "candidate",
            "trust": "host",
        }
    )
    swap = kdb.Replace(row["id"], values, run_id=case.run_id, why="contradicted")
    new = kdb.supersede(conn, table, swap)
    if new is None:
        return None
    return _transition(case, "superseded", "replaced by #%d" % new)


def _stale(conn: Any, case: Case) -> Transition | None:
    move = kdb.Status(
        case.row["id"], "stale", None, run_id=case.run_id, why="contradicted"
    )
    if kdb.set_status(conn, case.table, move):
        return _transition(case, "stale", "contradicted, no proposal")
    return None


def _one(conn: Any, case: Case) -> Transition | None:
    item = "%s:%d" % (case.table, case.row["id"])
    proposal = knowledge_feedback.latest_proposal(conn, item)
    if TEXT_COLUMN.get(case.table) and proposal.get("proposed"):
        got = _replace(conn, case, proposal)
        if got is not None:
            return got
    return _stale(conn, case)


def _resolve_contradicted(conn: Any, run_id: str, table: str) -> list:
    done: list = []
    for row in _contradicted_rows(conn, table):
        got = _one(conn, Case(table, row, {}, run_id))
        if got is not None:
            done.append(got)
    return done
