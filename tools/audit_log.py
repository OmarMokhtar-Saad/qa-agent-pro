"""Append-only audit log (LT-1 Phase 2 / I-039).

Built early so every later feature is "born audited": each meaningful event
(suite generated, exported, pushed to a TMS, bug reported) is recorded with a
timestamp, actor, entity id, and a small JSON detail blob. Multi-team / audited
deployments need this trail; wiring it now avoids retrofitting call sites later.

Contract (never-raises), mirroring tools/suite_store.py:
  On success: {"error": None, "content": <value>}
  On failure: {"error": str, "content": None}

All blocking sqlite I/O runs inside asyncio.to_thread(). A corrupt/unwritable DB
degrades to an error result (the caller carries on) rather than raising.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    event_type TEXT NOT NULL,
    actor      TEXT,
    entity_id  TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
"""


def _connect() -> sqlite3.Connection:
    path = Path(settings.qa_audit_log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    return conn


def _record_sync(
    event_type: str, actor: str | None, entity_id: str | None, detail: dict
) -> int:
    conn = _connect()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO events (ts, event_type, actor, entity_id, detail_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    time.time(),
                    event_type,
                    actor,
                    entity_id,
                    json.dumps(detail or {}, ensure_ascii=False),
                ),
            )
            return int(cur.lastrowid)
    finally:
        conn.close()


def _recent_sync(limit: int, event_type: str | None) -> list[dict]:
    conn = _connect()
    try:
        if event_type:
            rows = conn.execute(
                "SELECT ts, event_type, actor, entity_id, detail_json FROM events "
                "WHERE event_type = ? ORDER BY id DESC LIMIT ?",
                (event_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT ts, event_type, actor, entity_id, detail_json FROM events "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    finally:
        conn.close()
    out: list[dict] = []
    for ts, etype, actor, entity_id, detail_json in rows:
        try:
            detail = json.loads(detail_json)
        except Exception:
            detail = {}
        out.append(
            {
                "ts": ts,
                "event_type": etype,
                "actor": actor,
                "entity_id": entity_id,
                "detail": detail,
            }
        )
    return out


async def record_event(
    event_type: str,
    actor: str | None = None,
    entity_id: str | None = None,
    detail: dict | None = None,
) -> dict:
    """Append one audit event. Returns {"content": {"id": int}}. Never raises."""
    try:
        event_id = await asyncio.to_thread(
            _record_sync, event_type, actor, entity_id, detail or {}
        )
        return {"error": None, "content": {"id": event_id}}
    except Exception as exc:
        logger.exception("audit_log.record_event failed")
        return {"error": str(exc), "content": None}


async def recent_events(limit: int = 50, event_type: str | None = None) -> dict:
    """Return up to `limit` most recent events (newest first). Never raises."""
    try:
        rows = await asyncio.to_thread(_recent_sync, max(1, int(limit)), event_type)
        return {"error": None, "content": rows}
    except Exception as exc:
        logger.exception("audit_log.recent_events failed")
        return {"error": str(exc), "content": None}
