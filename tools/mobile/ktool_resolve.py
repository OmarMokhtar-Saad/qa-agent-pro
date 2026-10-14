"""``qa_mobile_knowledge`` resolve action: settle a disputed note."""

from __future__ import annotations

import json
from functools import partial

from tools.mobile import app_knowledge
from tools.mobile import knowledge_db as kdb
from tools.mobile import knowledge_gate as gate
from tools.mobile.ktool_args import Found, Ruling, Scope
from tools.mobile.ktool_core import _clone, _refuse, _row_of, _said, _write


def _settle(conn, number, ruling: Ruling) -> None:
    status = "retired" if ruling.verdict == "delete" else "active"
    change = kdb.Status(
        number,
        status,
        {"proposal_json": ""},
        run_id=ruling.run_id,
        why="resolved: " + ruling.verdict,
    )
    kdb.set_status(conn, "notes", change)


def _resolve_work(conn, tgt, ruling: Ruling) -> str:
    row = _row_of(conn, "notes", tgt[1])
    if row is None or not (row["proposal_json"] or row["status"] == "disputed"):
        return _refuse("note #%s is not disputed" % tgt[1])
    ruling = ruling._replace(prop=json.loads(row["proposal_json"] or "{}"))
    if ruling.verdict in ("delete", "keep"):
        _settle(conn, tgt[1], ruling)
    else:
        problem = _apply_proposal(conn, row, ruling)
        if problem:
            return _refuse(problem)
    return _said("note #%s resolved: %s" % (tgt[1], ruling.verdict))


def _resolve(scope: Scope, tgt, verdict: str) -> str:
    verdict = str(verdict or "").lower()
    if tgt[0] != "notes" or verdict not in ("keep", "replace", "narrow", "delete"):
        return _refuse(
            "`resolve` needs a notes item and `verdict` keep, replace, narrow or delete"
        )
    ruling = Ruling(verdict, {}, scope.run_id)
    return _write(scope.package, partial(_resolve_work, tgt=tgt, ruling=ruling))


def _variant_note(found: Found, env: dict, run_id: str) -> dict:
    values = gate._variant_values("notes", found.row, gate.Variant(found.got[0], env))
    values.update(trust="tester", status="active", source_run=run_id, runs_json="[]")
    return values


def _narrow(conn, found: Found, ruling: Ruling) -> str:
    env = ruling.prop.get("env") or {}
    if not env:
        return "the dispute names no context to narrow to"
    run_id = ruling.run_id
    values = _variant_note(found, env, run_id)
    kdb.set_status(
        conn,
        "notes",
        kdb.Status(
            found.row["id"],
            "active",
            {"proposal_json": ""},
            run_id=run_id,
            why="resolved: narrow",
        ),
    )
    write = kdb.Write(values, run_id=run_id, why="resolved: narrow")
    return "" if kdb.insert(conn, "notes", write) else "the variant was refused"


def _replace(conn, found: Found, ruling: Ruling) -> str:
    row, run_id = found.row, ruling.run_id
    values = {
        **_clone(row, "text", found.got[0]),
        "trust": "tester",
        "status": "active",
        "source_run": run_id,
        "runs_json": "[]",
        "proposal_json": "",
    }
    change = kdb.Replace(row["id"], values, run_id=run_id, why="resolved: replace")
    done = kdb.supersede(conn, "notes", change)
    return "" if done else "the replacement was refused"


def _apply_proposal(conn, row, ruling: Ruling) -> str:
    """``""`` on success, else why a replace/narrow could not be made."""
    text = " ".join(str(ruling.prop.get("proposed") or "").split())
    if not text:
        return "the dispute holds no proposal"
    got = app_knowledge._validate(
        (
            text,
            row["kind"],
            json.loads(row["when_json"] or "{}"),
            json.loads(row["then_json"] or "{}"),
        )
    )
    if isinstance(got, str):
        return got
    step = _narrow if ruling.verdict == "narrow" else _replace
    return step(conn, Found(row, got), ruling)
