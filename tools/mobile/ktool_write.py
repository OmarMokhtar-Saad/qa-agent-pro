"""``qa_mobile_knowledge`` write actions: confirm, reject, edit and rollback."""

from __future__ import annotations

import json
from functools import partial

from tools.mobile import app_knowledge, knowledge_schema
from tools.mobile import knowledge_db as kdb
from tools.mobile.ktool_args import Edit, Scope
from tools.mobile.ktool_core import (
    _EDITABLE,
    _clone,
    _refuse,
    _row_of,
    _said,
    _write,
)
from tools.untrusted import single_line


def _confirmed_status(row) -> str:
    if row["status"] in ("candidate", "needs_recheck", "stale"):
        return "active"
    return row["status"]


def _confirm_fields(row, run_id: str) -> dict:
    return {
        "confirmed": int(row["confirmed"] or 0) + 1,
        "runs_json": kdb._bump_runs(row["runs_json"], run_id),
    }


def _confirm_work(conn, tgt, run_id: str) -> str:
    row = _row_of(conn, *tgt)
    if row is None:
        return _refuse("no open item %s:%s" % tgt)
    status = _confirmed_status(row)
    fields = _confirm_fields(row, run_id)
    change = kdb.Status(
        tgt[1], status, fields, run_id=run_id, why="confirmed by the tester"
    )
    kdb.set_status(conn, tgt[0], change)
    return _said("%s:%s is %s (confirmed)" % (tgt[0], tgt[1], status))


def _confirm(package: str, tgt, run_id: str) -> str:
    return _write(package, partial(_confirm_work, tgt=tgt, run_id=run_id))


def _reject(scope: Scope, tgt, reason: str) -> str:
    def work(conn):
        if _row_of(conn, *tgt) is None:
            return _refuse("no open item %s:%s" % tgt)
        why = single_line(reason, 200)
        if app_knowledge.secret_reason(why, (), scope.run_id):
            why = ""
        kdb.set_status(
            conn,
            tgt[0],
            kdb.Status(
                tgt[1], "retired", None, run_id=scope.run_id, why=why or "rejected"
            ),
        )
        return _said("%s:%s retired" % tgt)

    return _write(scope.package, work)


def _edit_clean(row, table: str, text: str):
    """``(clean text, None)``, or ``("", why)`` when a note edit is invalid."""
    clean = " ".join(str(text).split())
    if table != "notes":
        return clean, None
    got = app_knowledge._validate(
        (
            clean,
            row["kind"],
            json.loads(row["when_json"] or "{}"),
            json.loads(row["then_json"] or "{}"),
        )
    )
    return ("", got) if isinstance(got, str) else (got[0], None)


def _edited_values(row, edit: Edit, clean: str) -> dict:
    return {
        **_clone(row, edit.column, clean),
        "trust": "tester",
        "status": "active",
        "source_run": edit.run_id,
        "runs_json": "[]",
    }


def _edit_work(conn, tgt, edit: Edit) -> str:
    table, number = tgt
    row = _row_of(conn, table, number)
    if row is None:
        return _refuse("no open item %s:%s" % tgt)
    clean, problem = _edit_clean(row, table, edit.text)
    if problem is not None:
        return _refuse(problem)
    values = _edited_values(row, edit, clean)
    change = kdb.Replace(number, values, run_id=edit.run_id, why="edited by the tester")
    new = kdb.supersede(conn, table, change)
    if not new:
        return _refuse("the edit was refused")
    return _said("%s:%s replaced by %s:%s" % (table, number, table, new))


def _edit(scope: Scope, tgt, text: str) -> str:
    column = _EDITABLE.get(tgt[0])
    if column is None or not str(text or "").strip():
        return _refuse("`edit` needs `text`, and works on notes, lessons and mistakes")
    work = partial(_edit_work, tgt=tgt, edit=Edit(column, text, scope.run_id))
    return _write(scope.package, work)


def _rollback(package: str, run_id: str) -> str:
    if not run_id:
        return _refuse("`rollback` needs `run_id`")

    def work(conn):
        got = knowledge_schema.rollback_run(conn, run_id)
        return _said(
            "rolled back %s row change(s) of run %s" % (got.get("undone", 0), run_id)
        )

    return _write(package, work)
