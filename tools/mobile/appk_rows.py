"""Rows of the knowledge store: events, note shape, caps, insert."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, NamedTuple

from tools.mobile import appk_consts


class Event(NamedTuple):
    """One row for the events table: its name, detail and run id."""

    name: str
    detail: Any = ""
    run_id: Any = ""


# Only slice-1 events count; knowledge_db events carry a table_name and are
# pruned by knowledge_db.compact instead. Feedback rows (capped by
# knowledge_feedback), learn markers and rollback rows have an empty
# table_name too but are not notes' events, so they never use this budget.
def _add_event(conn, note_id, event: Event) -> bool:
    (count,) = conn.execute(
        "SELECT COUNT(*) FROM events WHERE (table_name IS NULL OR table_name = '')"
        " AND event NOT IN ('feedback', 'learn_marker', 'rollback')"
    ).fetchone()
    if count >= appk_consts.MAX_EVENT_ROWS:
        return False
    conn.execute(
        "INSERT INTO events (ts, note_id, event, detail, run_id) VALUES (?, ?, ?, ?, ?)",
        (
            time.time(),
            note_id,
            event.name,
            str(event.detail or "")[:200],
            str(event.run_id or "")[:64],
        ),
    )
    return True


def _load(raw: Any) -> dict:
    try:
        value = json.loads(str(raw or "{}"))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _extra_keys(row: sqlite3.Row) -> dict:
    """Schema-v2 columns when the file has them (a v1 file has none)."""
    have = row.keys()
    return {
        name: row[name]
        for name in ("trust", "screen_key", "element_fp", "recheck_at")
        if name in have
    }


def _note(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "text": row["text"],
        "when": _load(row["when_json"]),
        "then": _load(row["then_json"]),
        "status": row["status"],
        "confirmed": row["confirmed"],
        "contradicted": row["contradicted"],
        "app_version": row["app_version"],
        "source_run": row["source_run"],
        "supersedes": row["supersedes"],
        **_extra_keys(row),
    }


def _counts(conn) -> tuple:
    (active,) = conn.execute(
        "SELECT COUNT(*) FROM notes WHERE status = 'active'"
    ).fetchone()
    (total,) = conn.execute("SELECT COUNT(*) FROM notes").fetchone()
    return active, total


def _make_room(conn, total: int) -> int:
    # Full: make room by dropping the OLDEST retired or superseded rows
    # (and their events). An active note is never deleted.
    stale = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM notes WHERE status != 'active' ORDER BY id LIMIT ?",
            (total - appk_consts.MAX_NOTE_ROWS + 1,),
        )
    ]
    for old_id in stale:
        conn.execute("DELETE FROM events WHERE note_id = ?", (old_id,))
        conn.execute("DELETE FROM notes WHERE id = ?", (old_id,))
    return len(stale)


def _cap_error(conn, superseding: int) -> str:
    """The refusal text when a new note would break a cap, else ``""``."""
    active, total = _counts(conn)
    if active - superseding >= appk_consts.MAX_ACTIVE_NOTES:
        return (
            "this app already has %d active notes: retire one first "
            "(qa_mobile_notes action=retire)" % appk_consts.MAX_ACTIVE_NOTES
        )
    if total >= appk_consts.MAX_NOTE_ROWS:
        if total - _make_room(conn, total) >= appk_consts.MAX_NOTE_ROWS:
            return (
                "this app's note history is full (%d rows, all active): "
                "retire one first (qa_mobile_notes action=retire)"
                % appk_consts.MAX_NOTE_ROWS
            )
    return ""


def _insert_note(conn, values: tuple, old: list) -> int:
    """Insert the row, mark the *old* ids superseded, return the new id.

    *values* ends with ``now``, the ``valid_from`` and ``invalid_at`` time."""
    cur = conn.execute(
        "INSERT INTO notes (kind, text, when_json, then_json, scope_key, "
        "app_version, source_run, supersedes, valid_from) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        values,
    )
    for old_id in old:
        conn.execute(
            "UPDATE notes SET status = 'superseded', invalid_at = ? WHERE id = ?",
            (values[-1], old_id),
        )
    return cur.lastrowid
