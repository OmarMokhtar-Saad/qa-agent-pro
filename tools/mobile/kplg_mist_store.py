"""Mistakes plug storage: the snapshot rows and the buffered-screen writes."""

from __future__ import annotations

import json
from typing import NamedTuple

from tools.mobile import app_knowledge, knowledge_db, knowledge_limits

_COLS = (
    "id",
    "screen_key",
    "element_fp",
    "bad_pattern",
    "symptom",
    "fix",
    "count",
    "trust",
    "status",
)


class Seen(NamedTuple):
    version: str
    run_id: str


def _mistakes(conn) -> dict:
    out: dict = {}
    where = "status = 'active' AND invalid_at IS NULL"
    for row in knowledge_db.rows(
        conn,
        "mistakes",
        knowledge_db.Where(where, (), knowledge_limits.SNAPSHOT_MAX_ROWS),
    ):
        item = {col: row.get(col) for col in _COLS}
        item["table"] = "mistakes"
        out.setdefault(str(row.get("screen_key") or ""), []).append(item)
    return out


def _bindings(conn) -> dict:
    out: dict = {}
    where = "status = 'active' AND invalid_at IS NULL AND bound_field != ''"
    for row in knowledge_db.rows(
        conn,
        "elements",
        knowledge_db.Where(where, (), knowledge_limits.SNAPSHOT_MAX_ROWS),
    ):
        try:
            rid = str(json.loads(row.get("fp_json") or "{}").get("rid") or "")
        except ValueError:
            continue
        if rid:
            out.setdefault(str(row.get("screen_key") or ""), {})[rid] = {
                "field": row["bound_field"],
                "id": row["id"],
            }
    return out


def snapshot(conn, facts) -> dict:
    return {"mistakes": _mistakes(conn), "bindings": _bindings(conn)}


def screens(events: list) -> list:
    """The buffered ``screen`` events."""
    return [
        ev for ev in events or [] if isinstance(ev, dict) and ev.get("e") == "screen"
    ]


def open_store(package: str):
    """A writable store connection, or ``None`` for an app with no store."""
    path = app_knowledge.db_path(package)
    if path is None or not path.exists():
        return None  # an app with no store stays without one
    return knowledge_db.open_rw(package)


def _version(conn, run_id: str) -> str:
    row = conn.execute(
        "SELECT app_version FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    return str(row[0] or "") if row else ""


def _screen_row(ev: dict, seen: Seen) -> knowledge_db.Write:
    return knowledge_db.Write(
        {
            "screen_key": str(ev.get("key") or ""),
            "activity": str(ev.get("activity") or ""),
            "anchors_json": json.dumps(list(ev.get("anchors") or [])),
            "app_version": seen.version,
            "status": "active",
            "source_run": seen.run_id,
        },
        run_id=seen.run_id,
        why="screen seen",
    )


def write_screens(conn, run_id: str, shots: list) -> None:
    seen = Seen(_version(conn, run_id), run_id)
    for ev in shots:
        knowledge_db.insert(conn, "screens", _screen_row(ev, seen))
