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
from dataclasses import dataclass
from typing import cast

from tools import audit_port, settings_port, sqlite_store
from tools.install_paths import resolve_data_path

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

_CONNECT_OPTS = sqlite_store.ConnectOpts(
    timeout=5.0,
    wal=True,
    busy_timeout_ms=5000,
    pragmas_best_effort=True,
    logger=logging.getLogger(__name__),
    log_label="audit_log",
)


def _connect() -> sqlite3.Connection:
    # Install-root anchored -- see tools/install_paths. A cwd-relative audit log
    # split the trail across databases, so "what happened" needed archaeology.
    path = resolve_data_path(settings_port.get().qa_audit_log_path)
    conn = sqlite_store.connect(
        path,
        _CONNECT_OPTS,
    )
    try:
        conn.executescript(_SCHEMA)
    except BaseException:
        conn.close()
        raise
    return conn


@dataclass(frozen=True)
class AuditEvent:
    """One audit event; build with keyword arguments."""

    event_type: str
    actor: str | None = None
    entity_id: str | None = None
    detail: dict | None = None


def _record_sync(ev: AuditEvent) -> int:
    conn = _connect()
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO events (ts, event_type, actor, entity_id, detail_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    time.time(),
                    ev.event_type,
                    ev.actor,
                    ev.entity_id,
                    json.dumps(ev.detail or {}, ensure_ascii=False),
                ),
            )
            return int(cast("int", cur.lastrowid))
    finally:
        conn.close()


def _query_rows(limit: int, event_type: str | None) -> list:
    conn = _connect()
    try:
        if event_type:
            return conn.execute(
                "SELECT ts, event_type, actor, entity_id, detail_json FROM events "
                "WHERE event_type = ? ORDER BY id DESC LIMIT ?",
                (event_type, limit),
            ).fetchall()
        return conn.execute(
            "SELECT ts, event_type, actor, entity_id, detail_json FROM events "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()


def _decode_row(row: tuple) -> dict:
    ts, etype, actor, entity_id, detail_json = row
    try:
        detail = json.loads(detail_json)
    except (ValueError, TypeError):
        detail = {}
    return {
        "ts": ts,
        "event_type": etype,
        "actor": actor,
        "entity_id": entity_id,
        "detail": detail,
    }


def _recent_sync(limit: int, event_type: str | None) -> list[dict]:
    return [_decode_row(row) for row in _query_rows(limit, event_type)]


async def record_event(ev: AuditEvent) -> dict:
    """Append one audit event. Returns {"content": {"id": int}}. Never raises."""
    try:
        event_id = await asyncio.to_thread(_record_sync, ev)
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


async def _bound_writer(ev: AuditEvent) -> dict:
    # Trampoline: resolve ``record_event`` through the module global at call
    # time so a test that patches ``tools.audit_log.record_event`` still bites.
    return await record_event(ev)


audit_port.bind(_bound_writer)
