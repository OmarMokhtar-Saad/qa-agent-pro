"""Shortcuts plug persistence: the snapshot rows and the fired/fail flush."""

from __future__ import annotations

import json
from typing import Any, NamedTuple

from tools.mobile import knowledge_db, knowledge_limits, knowledge_stage_edges


class Shot(NamedTuple):
    run_id: str
    ev: dict
    row: dict


def _load(raw: Any, default: Any) -> Any:
    try:
        return json.loads(raw or "")
    except ValueError:
        return default


def _shortcut_of(row: dict) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "start_key": row["start_key"],
        "end_key": row["end_key"],
        "steps": _load(row["steps_json"], []),
        "precondition": _load(row["precondition_json"], {}),
    }


def snapshot(conn, facts) -> dict:
    """Active shortcuts (``[{id, name, start_key, steps, precondition}]``) and active edges."""

    where = "status = 'active' AND invalid_at IS NULL AND start_key != ''"
    rows = knowledge_db.rows(
        conn,
        "shortcuts",
        knowledge_db.Where(where, (), knowledge_limits.SNAPSHOT_MAX_ROWS),
    )
    return {
        "shortcuts": [_shortcut_of(row) for row in rows],
        "edges": knowledge_stage_edges.snapshot(conn, facts),
    }


def _row_of(conn, item: Any) -> dict | None:
    parsed = knowledge_db.parse_item_id(item)
    if parsed is None or parsed[0] != "shortcuts":
        return None
    found = knowledge_db.rows(
        conn,
        "shortcuts",
        knowledge_db.Where("id = ? AND invalid_at IS NULL", (parsed[1],), 1),
    )
    return found[0] if found else None


def _success(conn, shot: Shot) -> None:
    if shot.ev.get("refused") or int(shot.ev.get("left") or 0) > 0:
        return  # never ran to the end: not inserted, or the run stopped
    knowledge_db.upsert_counter(
        conn,
        "shortcuts",
        knowledge_db.Counter(
            {"name": shot.row["name"]},
            {"success": 1},
            run_id=shot.run_id,
            why="shortcut fired",
        ),
    )


def _stale_check(conn, shot: Shot) -> None:
    if int(shot.row.get("fail") or 0) + 1 >= knowledge_limits.STALE_AFTER_FAILS:
        knowledge_db.set_status(
            conn,
            "shortcuts",
            knowledge_db.Status(
                shot.row["id"],
                "stale",
                None,
                run_id=shot.run_id,
                why="shortcut failed repeatedly",
            ),
        )


def _failed(conn, shot: Shot) -> None:
    knowledge_db.upsert_counter(
        conn,
        "shortcuts",
        knowledge_db.Counter(
            {"name": shot.row["name"]},
            {"fail": 1},
            run_id=shot.run_id,
            why="shortcut " + str(shot.ev.get("why", ""))[:40],
        ),
    )
    _stale_check(conn, shot)


def _apply(conn, run_id: str, ev: dict) -> None:
    row = _row_of(conn, ev.get("item"))
    if row is None:
        return
    shot = Shot(run_id, ev, row)
    if ev.get("kind") == "shortcut_fired":
        _success(conn, shot)
    else:
        _failed(conn, shot)


def flush(package: str, run_id: str, events: list) -> None:
    """Persist this run's shortcut fired/fail events (one connection, batched)."""
    mine = [
        e
        for e in events or []
        if isinstance(e, dict) and e.get("kind") in ("shortcut_fired", "shortcut_fail")
    ]
    if not mine:
        return
    conn = knowledge_db.open_rw(package)
    if conn is None:
        return
    try:
        for ev in mine:
            _apply(conn, run_id, ev)
    finally:
        conn.close()
