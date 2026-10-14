from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any, NamedTuple, cast

from tools.mobile.knowledge_schema_ddl import LEARNED_TABLES, columns

logger = logging.getLogger("tools.mobile.knowledge_schema")


class RowRef(NamedTuple):
    table: str
    row_id: int


class UndoEvent(NamedTuple):
    kind: str
    table: str
    row_id: int
    raw: str


def _load(raw: Any) -> dict:
    try:
        value = json.loads(str(raw or "{}"))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _restore(conn: sqlite3.Connection, ref: RowRef, before: dict) -> bool:
    cols = set(columns(conn, ref.table))
    items = [(k, v) for k, v in before.items() if k in cols and k != "id"]
    if not items:
        return False
    sets = ", ".join("%s = ?" % k for k, _ in items)
    conn.execute(
        "UPDATE %s SET %s WHERE id = ?" % (ref.table, sets),
        [v for _, v in items] + [ref.row_id],
    )
    return True


def _retire(conn: sqlite3.Connection, ref: RowRef) -> None:
    conn.execute(
        "UPDATE %s SET status = 'retired', invalid_at = COALESCE(invalid_at, ?) WHERE id = ?"
        % ref.table,
        (time.time(), ref.row_id),
    )


def _undo_supersede(conn: sqlite3.Connection, ref: RowRef, before: dict) -> None:
    _retire(conn, ref)
    old = cast("dict", before.get("old") if isinstance(before.get("old"), dict) else {})
    old_id = old.get("id")
    if isinstance(old_id, int):
        _restore(conn, RowRef(ref.table, old_id), old)


def _undo_one(conn: sqlite3.Connection, ev: UndoEvent) -> bool:
    """Undo one event. ``kind`` is the event name written by knowledge_db."""
    before = _load(ev.raw)
    ref = RowRef(ev.table, ev.row_id)
    if ev.kind == "insert":
        _retire(conn, ref)
        return True
    if ev.kind == "supersede":
        _undo_supersede(conn, ref, before)
        return True
    if ev.kind in ("update", "counter", "status"):
        return _restore(conn, ref, before)
    return False


def _last_rollback(conn: sqlite3.Connection, run_id: str) -> int:
    return conn.execute(
        "SELECT COALESCE(MAX(id), 0) FROM events WHERE run_id = ? AND event = 'rollback'",
        (run_id,),
    ).fetchone()[0]


def _events_after(conn: sqlite3.Connection, run_id: str, last: int) -> list:
    return conn.execute(
        "SELECT event, table_name, row_id, before_json FROM events "
        "WHERE run_id = ? AND id > ? AND table_name != '' AND row_id IS NOT NULL "
        "ORDER BY id DESC",
        (run_id, last),
    ).fetchall()


def _undo_all(conn: sqlite3.Connection, rows: list) -> int:
    undone = 0
    for row in rows:
        ev = UndoEvent(str(row[0]), str(row[1]), int(row[2]), str(row[3] or ""))
        if ev.table in LEARNED_TABLES and _undo_one(conn, ev):
            undone += 1
    return undone


def _log_rollback(conn: sqlite3.Connection, run_id: str, undone: int) -> None:
    conn.execute(
        "INSERT INTO events (ts, note_id, event, detail, run_id, table_name, row_id, before_json) "
        "VALUES (?, NULL, 'rollback', ?, ?, '', NULL, '')",
        (time.time(), "undone %d" % undone, run_id[:64]),
    )


def _failed(conn: sqlite3.Connection, exc: Exception) -> dict:
    logger.warning("knowledge schema: rollback failed", exc_info=True)
    try:
        conn.rollback()
    except sqlite3.Error:
        pass
    return {"undone": 0, "error": "rollback failed: %s" % type(exc).__name__}


def rollback_run(conn: sqlite3.Connection, run_id: str) -> dict:
    """Undo everything *run_id* wrote, newest first. Never raises.

    Returns ``{"undone": n}`` (plus ``"error"`` on failure). A second call is a
    no-op: events older than the run's last ``rollback`` event are skipped."""
    try:
        run_id = str(run_id or "")
        if not run_id:
            return {"undone": 0, "error": "no run id"}
        rows = _events_after(conn, run_id, _last_rollback(conn, run_id))
        undone = _undo_all(conn, rows)
        _log_rollback(conn, run_id, undone)
        conn.commit()
        return {"undone": undone}
    except (sqlite3.Error, ValueError, TypeError, KeyError, OSError) as exc:
        return _failed(conn, exc)
