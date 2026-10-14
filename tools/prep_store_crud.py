"""Prep record save/load/delete/update/touch (leaf of ``tools.prep_store``)."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid

from tools.prep_store_cfg import (
    _expired,
    _max_bytes,
    _max_lifetime_s,
    _payload_too_big,
)
from tools.prep_store_conn import _connect, _has_touched_at, _purge_expired_sync

logger = logging.getLogger("tools.prep_store")


def _log_purged(purged: int) -> None:
    if purged:
        logger.info(
            "prep_store: purged %d prep(s) past the %ds lifetime cap",
            purged,
            int(_max_lifetime_s()),
        )


# Sweep on WRITE, inside the same transaction. Not in _connect():
# that runs on every read too, and a DELETE on the read path would
# turn a concurrent reader into a writer and contend for the WAL
# lock. A prepare is the natural, low-frequency sweep point, and it
# is already a write. The preps table is small (hundreds of rows),
# so the scan is bounded in practice.
def _save_prep_sync(payload: dict, created_by: str | None) -> str:
    prep_id = uuid.uuid4().hex
    now = time.time()
    conn = _connect()
    try:
        with conn:  # transaction
            conn.execute(
                "INSERT INTO preps (id, payload_json, created_at, created_by) "
                "VALUES (?, ?, ?, ?)",
                (prep_id, json.dumps(payload), now, created_by),
            )
            purged = _purge_expired_sync(conn)
        _log_purged(purged)
        return prep_id
    finally:
        conn.close()


def _prep_row(conn: sqlite3.Connection, prep_id: str) -> tuple | None:
    # NEVER assume the migration ran: on a locked DB it is deferred, and
    # selecting a missing column would raise "no such column" -- which this
    # function converts into a miss, i.e. a LIVE prep reported as unknown.
    _sql = "SELECT payload_json, created_at, touched_at FROM preps WHERE id = ?"
    if not _has_touched_at(conn):
        _sql = "SELECT payload_json, created_at, NULL FROM preps WHERE id = ?"
    return conn.execute(_sql, (prep_id,)).fetchone()


def _load_prep_sync(prep_id: str) -> dict | None:
    conn = _connect()
    try:
        row = _prep_row(conn, prep_id)
        if row is None:
            return None
        if _expired(float(row[1]), row[2]):
            # Expired — delete it (and its submissions, via cascade) and miss.
            with conn:
                conn.execute("DELETE FROM preps WHERE id = ?", (prep_id,))
            return None
    finally:
        conn.close()
    try:
        return json.loads(row[0])
    except (ValueError, TypeError):
        return None


def _delete_prep_sync(prep_id: str) -> bool:
    """Delete the prep; True only when THIS call removed a row (atomic claim)."""
    conn = _connect()
    try:
        with conn:  # transaction
            cur = conn.execute("DELETE FROM preps WHERE id = ?", (prep_id,))
            return cur.rowcount > 0
    finally:
        conn.close()


def _update_prep_sync(prep_id: str, payload: dict) -> bool:
    now_payload = json.dumps(payload)
    conn = _connect()
    try:
        with conn:  # transaction
            cur = conn.execute(
                "UPDATE preps SET payload_json = ? WHERE id = ?",
                (now_payload, prep_id),
            )
        return (cur.rowcount or 0) > 0
    finally:
        conn.close()


# Rejects an over-large payload (``QA_PREP_MAX_BYTES``) so a host cannot wedge
# the store with a pathological submission. Never raises.
async def save_prep(payload: dict, created_by: str | None = None) -> dict:
    """Persist a prepared-generation record. Returns {"content": {"prep_id": ...}}."""
    try:
        if _payload_too_big(payload):
            return {
                "error": f"prep payload exceeds {_max_bytes()} bytes",
                "content": None,
            }
        prep_id = await asyncio.to_thread(_save_prep_sync, payload or {}, created_by)
        logger.info("prep_store: saved prep %s", prep_id)
        return {"error": None, "content": {"prep_id": prep_id}}
    except Exception as exc:
        logger.exception("prep_store.save_prep failed")
        return {"error": str(exc), "content": None}


# Content is ``None`` for an unknown, tampered (uuid miss), or EXPIRED id (an
# expired record is deleted on this read). Never raises.
async def load_prep(prep_id: str) -> dict:
    """Load a prep record by id. Returns {"content": dict|None}."""
    try:
        if not prep_id:
            return {"error": None, "content": None}
        payload = await asyncio.to_thread(_load_prep_sync, prep_id)
        return {"error": None, "content": payload}
    except Exception as exc:
        logger.exception("prep_store.load_prep failed")
        return {"error": str(exc), "content": None}


async def delete_prep(prep_id: str) -> dict:
    """Delete a prep record (and its submissions, via cascade) once finalized."""
    try:
        deleted = False
        if prep_id:
            deleted = await asyncio.to_thread(_delete_prep_sync, prep_id)
        # ``deleted`` is False when another caller already removed the row, so a
        # one-shot consumer (host_llm.close_task) can refuse a concurrent second close.
        return {"error": None, "content": {"prep_id": prep_id, "deleted": deleted}}
    except Exception as exc:
        logger.exception("prep_store.delete_prep failed")
        return {"error": str(exc), "content": None}


# Used to persist the incremented gap-loop round in the envelope's ``meta`` so a
# host cannot ping-pong forever. Deliberately keeps ``created_at`` unchanged, so
# the TTL still counts from prepare and a looping host cannot extend it by
# resubmitting. Rejects an over-large payload and a missing/unknown/expired
# ``prep_id`` (the UPDATE simply matches no row). Never raises.
async def update_prep(prep_id: str, payload: dict) -> dict:
    """Overwrite an existing prep record's payload IN PLACE (host-mode gap loop)."""
    try:
        if not prep_id:
            return {"error": "prep_id is required", "content": None}
        if _payload_too_big(payload):
            return {
                "error": f"prep payload exceeds {_max_bytes()} bytes",
                "content": None,
            }
        updated = await asyncio.to_thread(_update_prep_sync, prep_id, payload or {})
        if not updated:
            return {"error": "unknown or expired prep_id", "content": None}
        return {"error": None, "content": {"prep_id": prep_id}}
    except Exception as exc:
        logger.exception("prep_store.update_prep failed")
        return {"error": str(exc), "content": None}
