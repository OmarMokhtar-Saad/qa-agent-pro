"""Shortcut row reads and writes (split out of knowledge_stage_shortcuts)."""

from __future__ import annotations

import json
from typing import NamedTuple

from tools.mobile import knowledge_db, knowledge_limits


class Seen(NamedTuple):
    run_id: str
    name: str


def _runs(row: dict) -> list:
    try:
        got = json.loads(row.get("runs_json") or "[]")
        return got if isinstance(got, list) else []
    except ValueError:
        return []


def _make_room(conn, run_id: str) -> None:
    (have,) = conn.execute("SELECT COUNT(*) FROM shortcuts").fetchone()
    if have < knowledge_limits.MAX_ROWS["shortcuts"]:
        return
    row = conn.execute(
        "SELECT id FROM shortcuts WHERE invalid_at IS NULL AND status = 'candidate'"
        " AND origin = 'mined' ORDER BY id ASC LIMIT 1"
    ).fetchone()
    if row is not None:
        knowledge_db.set_status(
            conn,
            "shortcuts",
            knowledge_db.Status(
                row["id"], "retired", None, run_id=run_id, why="shortcut cap"
            ),
        )


def _values(cand: tuple) -> dict:
    name, start, end, steps, args = cand
    return {
        "name": name,
        "origin": "mined",
        "status": "candidate",
        "steps_json": steps,
        "args_json": args,
        "precondition_json": {"screen_key": start, "rids": [steps[0]["rid"]]},
        "start_key": start,
        "end_key": end,
    }


def insert(conn, run_id: str, cand: tuple) -> int:
    _make_room(conn, run_id)
    write = knowledge_db.Write(_values(cand), run_id=run_id, why="shortcut mined")
    return 1 if knowledge_db.insert(conn, "shortcuts", write) else 0


def by_name(conn, name: str) -> list:
    where = knowledge_db.Where("name = ? AND invalid_at IS NULL", (name,), 1)
    return knowledge_db.rows(conn, "shortcuts", where)


def seen_again(conn, row: dict, s: Seen) -> tuple[int, int]:
    """``(rows written, gate-ready)`` for a candidate already stored."""
    wrote = ready = 0
    if s.run_id not in _runs(row):
        counter = knowledge_db.Counter(
            {"name": s.name}, {}, run_id=s.run_id, why="shortcut seen"
        )
        wrote = 1 if knowledge_db.upsert_counter(conn, "shortcuts", counter) else 0
    if (
        row["status"] == "candidate"
        and len(_runs(row)) + 1 >= knowledge_limits.SHORTCUT_MIN_RUNS
    ):
        ready = 1
    return wrote, ready
