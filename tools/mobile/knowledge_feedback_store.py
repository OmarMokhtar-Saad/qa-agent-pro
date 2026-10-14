"""Persist and read feedback events (the ``events`` table of the knowledge DB)."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any, NamedTuple

from tools.mobile import knowledge_db as kdb
from tools.mobile import knowledge_limits as limits
from tools.mobile.knowledge_feedback_core import EVENT

logger = logging.getLogger(__name__)

_INSERT_SQL = (
    "INSERT INTO events (ts, note_id, event, detail, run_id, table_name, row_id, before_json)"
    " VALUES (?, NULL, ?, ?, ?, '', NULL, ?)"
)
_PAYLOAD_KEYS = ("verdict", "observation", "proposed", "weight")


class Stamp(NamedTuple):
    """The run and the one timestamp every row of a flush shares."""

    run_id: str
    ts: float


def run_feedback(conn: Any, run_id: str) -> list:
    """The feedback events of *run_id* as dicts (item_id, verdict, observation,
    proposed, weight), oldest first. Read-only; ``[]`` on any error."""
    out: list = []
    try:
        found = conn.execute(
            "SELECT detail, before_json FROM events WHERE event = ? AND run_id = ? ORDER BY id",
            (EVENT, str(run_id)),
        ).fetchall()
        for row in found:
            try:
                body = json.loads(row[1] or "{}")
            except ValueError:
                continue
            if isinstance(body, dict):
                out.append({"item_id": str(row[0]), **body})
    except sqlite3.Error:
        logger.exception("knowledge feedback read failed")
    return out


def latest_proposal(conn: Any, item_id: str) -> dict:
    """The newest non-empty proposal ever reported for *item_id*, or ``{}``."""
    try:
        found = conn.execute(
            "SELECT before_json, run_id FROM events WHERE event = ? AND detail = ? ORDER BY id DESC LIMIT 20",
            (EVENT, str(item_id)),
        ).fetchall()
        for row in found:
            body = json.loads(row[0] or "{}")
            if isinstance(body, dict) and str(body.get("proposed") or "").strip():
                return {**body, "run_id": str(row[1])}
    except (sqlite3.Error, ValueError):
        logger.exception("knowledge feedback proposal read failed")
    return {}


def _key_of(data: dict) -> tuple | None:
    """``(kind, rid, screen, own id)`` of a note, or ``None`` when it names too little."""
    kind = str(data.get("kind") or "")
    rid = str((data.get("when") or {}).get("rid") or "")
    screen = str(data.get("screen_key") or "")
    if not kind or not (rid or screen):
        return None
    return kind, rid, screen, int(data.get("id") or 0)


def _find(conn: Any, key: tuple) -> int | None:
    kind, rid, screen, mine = key
    found = conn.execute(
        "SELECT id, when_json, screen_key FROM notes WHERE kind = ? AND invalid_at IS NULL"
        " AND status IN ('active', 'disputed', 'needs_recheck') AND id != ? ORDER BY id DESC LIMIT 200",
        (kind, mine),
    ).fetchall()
    for row in found:
        try:
            theirs = json.loads(row["when_json"] or "{}")
        except ValueError:
            continue
        if (
            str(row["screen_key"] or "") == screen
            and str(theirs.get("rid") or "") == rid
        ):
            return int(row["id"])
    return None


def match_existing(conn: Any, note: Any) -> int | None:
    """The id of an open note with the same (screen_key, element rid, kind), or ``None``."""
    try:
        key = _key_of(note if isinstance(note, dict) else {})
        return None if key is None else _find(conn, key)
    except (sqlite3.Error, TypeError, ValueError, IndexError):
        logger.exception("knowledge feedback match failed")
    return None


def _events(events: Any) -> list:
    return [e for e in events or [] if isinstance(e, dict) and e.get("kind") == EVENT]


def _payload(event: dict) -> str:
    return json.dumps({k: event.get(k) for k in _PAYLOAD_KEYS})


def _insert(conn: Any, rows: list, stamp: Stamp) -> None:
    for event in rows:
        conn.execute(
            _INSERT_SQL,
            (
                stamp.ts,
                EVENT,
                str(event.get("item_id") or "")[:60],
                str(stamp.run_id)[:64],
                _payload(event),
            ),
        )


def _trim(conn: Any) -> None:
    conn.execute(
        "DELETE FROM events WHERE id IN (SELECT id FROM events WHERE event = ?"
        " ORDER BY id DESC LIMIT -1 OFFSET ?)",
        (EVENT, limits.MAX_FEEDBACK_EVENTS),
    )


def _commit(conn: Any, rows: list, stamp: Stamp) -> int:
    conn.execute("BEGIN IMMEDIATE")
    _insert(conn, rows, stamp)
    _trim(conn)
    conn.commit()
    return len(rows)


def _rollback(conn: Any) -> None:
    try:
        conn.rollback()
    except sqlite3.Error:
        logger.exception("knowledge feedback rollback failed")


def flush(package: str, run_id: str, events: list) -> int:
    """Plug seam: persist the feedback events of one kbuf flush as ``feedback`` event
    rows (``detail`` = item id, ``before_json`` = the payload; ``table_name`` stays
    empty so a rollback never replays them). One batched write."""
    rows = _events(events)
    if not rows or not run_id:
        return 0
    conn = kdb.open_rw(package)
    if conn is None:
        return 0
    try:
        return _commit(conn, rows, Stamp(run_id, time.time()))
    except (sqlite3.Error, TypeError, ValueError):
        logger.exception("knowledge feedback flush failed")
        _rollback(conn)
        return 0
    finally:
        conn.close()
