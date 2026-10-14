"""Stage B, the answer side: validate the model's ops and apply them to the store."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from tools.mobile import app_knowledge, knowledge_limits
from tools.mobile import knowledge_db as kdb
from tools.untrusted import single_line

_ERRORS = (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, OSError)


def _one_op(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("op") or "").upper()
    text = " ".join(str(raw.get("text") or "").split())[
        : knowledge_limits.FEEDBACK_CHARS
    ]
    parsed = kdb.parse_item_id(raw.get("item_id"))
    if name == "ADD" and text:
        return {
            "op": name,
            "text": text,
            "screen_key": single_line(raw.get("screen_key"), 60),
        }
    if name == "EDIT" and text and parsed and parsed[0] == "lessons":
        return {"op": name, "text": text, "id": parsed[1]}
    if name in ("UPVOTE", "DOWNVOTE") and parsed and parsed[0] != "notes":
        return {"op": name, "text": "", "table": parsed[0], "id": parsed[1]}
    return None


def _scan(listing: list, dropped: int) -> tuple:
    ops: list = []
    for raw in listing:
        op = _one_op(raw)
        if op is None:
            dropped += 1
            continue
        reason = app_knowledge.secret_reason(
            op["text"] + "\n" + op.get("screen_key", "")
        )
        if reason:
            return [], dropped, reason
        ops.append(op)
    return ops, dropped, ""


def _clean_ops(answer: Any) -> tuple:
    """``(ops, dropped, secret)`` where secret names a refused text or is ``""``."""
    if isinstance(answer, str):
        answer = json.loads(answer)
    listing = answer.get("ops") if isinstance(answer, dict) else answer
    if not isinstance(listing, list):
        return [], 0, ""
    cap = knowledge_limits.REFLECT_MAX_OPS
    return _scan(listing[:cap], max(0, len(listing) - cap))


def _vote(conn: Any, op: dict, run_id: str) -> bool:
    row = conn.execute(
        "SELECT * FROM %s WHERE id = ? AND invalid_at IS NULL" % op["table"],
        (op["id"],),
    ).fetchone()
    if row is None or row["status"] in ("superseded", "retired"):
        return False
    up = op["op"] == "UPVOTE"
    cols: dict[str, Any] = {"runs_json": kdb._bump_runs(row["runs_json"], run_id)}
    cols["confirmed" if up else "contradicted"] = (
        int(row["confirmed" if up else "contradicted"] or 0) + 1
    )
    if op["table"] == "lessons":
        cols["votes"] = int(row["votes"] or 0) + (1 if up else -1)
    return kdb.set_status(
        conn,
        op["table"],
        kdb.Status(row["id"], row["status"], cols, run_id=run_id, why="reflection"),
    )


def _fresh(run_id: str) -> dict:
    """The columns every new lessons candidate from a reflection carries."""
    return {
        "trust": "host",
        "status": "candidate",
        "source_run": run_id,
        "runs_json": json.dumps([run_id]),
    }


def _apply(conn: Any, op: dict, run_id: str) -> bool:
    if op["op"] == "ADD":
        values = {"text": op["text"], "screen_key": op["screen_key"], **_fresh(run_id)}
        write = kdb.Write(values, run_id=run_id, why="reflection")
        return kdb.insert(conn, "lessons", write) is not None
    if op["op"] == "EDIT":
        new = {"text": op["text"], **_fresh(run_id)}
        swap = kdb.Replace(op["id"], new, run_id=run_id, why="reflection")
        return kdb.supersede(conn, "lessons", swap) is not None
    return _vote(conn, op, run_id)
