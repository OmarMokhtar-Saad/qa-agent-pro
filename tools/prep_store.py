"""Pending-generation store — SQLite-backed, never-raises (host-mode Phase 1).

Host-mode ("boomerang") test-case generation splits the pipeline across TWO
MCP tool calls: ``qa_prepare_test_cases`` builds a grounded prompt and hands it
to the tester's own chat model, then ``qa_submit_suite`` validates the JSON the
host produced. Because those are two separate stateless tool calls, the prepared
context (grounded prompt, checklist, category specs, case-count bounds, ticket
provenance) must persist BETWEEN them so submit validates against what was
actually prepared — and so a stale, unknown, or tampered ``prep_id`` is rejected
rather than trusted.

This module persists each prep record (and, for weaker hosts that submit one
category at a time, its incremental per-category submissions) to the SAME
SQLite file ``tools/suite_store.py`` uses, in its own tables. It mirrors
suite_store's never-raise contract exactly:

  On success: {"error": None, "content": <value>}
  On failure: {"error": str, "content": None}

All blocking sqlite I/O runs in ``asyncio.to_thread()``. Records carry a
creation timestamp and are expired on read (TTL, ``QA_PREP_TTL_S``). ``prep_id``
is an unguessable uuid4 hex, so an unknown/tampered id simply misses and load
returns ``None`` — the caller (``qa_submit_suite``) then refuses to finalize.
Payloads over ``QA_PREP_MAX_BYTES`` are rejected at save time so a host cannot
wedge the store.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)

_DEFAULT_TTL_S = 3600
_DEFAULT_MAX_BYTES = 4_000_000

# Seconds a contended write waits before giving up. Host mode means several MCP
# host processes share this DB file, so a short wait beats an instant failure.
_BUSY_TIMEOUT_S = 5.0


_SCHEMA = """
CREATE TABLE IF NOT EXISTS preps (
    id           TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    created_at   REAL NOT NULL,
    created_by   TEXT
);
CREATE TABLE IF NOT EXISTS prep_submissions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    prep_id       TEXT NOT NULL,
    category_name TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    created_at    REAL NOT NULL,
    UNIQUE(prep_id, category_name),
    FOREIGN KEY (prep_id) REFERENCES preps(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_prep_submissions_prep_id
    ON prep_submissions(prep_id);
"""


def _db_path() -> Path:
    return Path(settings.qa_suite_store_path)


def _ttl_seconds() -> float:
    """TTL after which a prep record is expired on read. Never raises."""
    val = getattr(settings, "qa_prep_ttl_s", _DEFAULT_TTL_S)
    try:
        val = int(val)
    except (TypeError, ValueError):
        return float(_DEFAULT_TTL_S)
    return float(val) if val > 0 else float(_DEFAULT_TTL_S)


def _max_bytes() -> int:
    """Max serialized payload size accepted by save_prep/save_submission."""
    val = getattr(settings, "qa_prep_max_bytes", _DEFAULT_MAX_BYTES)
    try:
        val = int(val)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_BYTES
    return val if val > 0 else _DEFAULT_MAX_BYTES


def _connect() -> sqlite3.Connection:
    """Open the DB (creating parent dirs + schema). Caller must close.

    PRAGMA foreign_keys = ON so the declared ON DELETE CASCADE on
    prep_submissions actually fires (SQLite defaults it OFF per connection).

    journal_mode = WAL + a busy_timeout because host mode makes CONCURRENT
    multi-client use the normal case, not the exception: each MCP host (Claude
    Desktop, Cursor, ChatGPT, ...) runs its own server process against this same
    SQLite file, so two testers preparing/submitting at the same moment would
    otherwise race on the default rollback journal and surface "database is
    locked". WAL lets readers and one writer proceed concurrently; the timeout
    makes a genuinely contended write wait instead of failing instantly. Both
    pragmas are best-effort — an older SQLite or a filesystem that cannot do WAL
    must not break the store, so a failure here is logged and ignored (the
    never-raise contract still holds via the callers' error dicts).
    """
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=_BUSY_TIMEOUT_S)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(f"PRAGMA busy_timeout = {int(_BUSY_TIMEOUT_S * 1000)}")
    except sqlite3.Error:
        logger.debug("prep_store: WAL/busy_timeout pragmas unavailable", exc_info=True)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    return conn


# --------------------------------------------------------------------------- #
# Sync workers (run inside asyncio.to_thread)
# --------------------------------------------------------------------------- #


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
        return prep_id
    finally:
        conn.close()


def _load_prep_sync(prep_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT payload_json, created_at FROM preps WHERE id = ?", (prep_id,)
        ).fetchone()
        if row is None:
            return None
        if (time.time() - float(row[1])) > _ttl_seconds():
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


def _delete_prep_sync(prep_id: str) -> None:
    conn = _connect()
    try:
        with conn:  # transaction
            conn.execute("DELETE FROM preps WHERE id = ?", (prep_id,))
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


def _save_submission_sync(prep_id: str, category_name: str, payload: dict) -> int:
    now = time.time()
    conn = _connect()
    try:
        with conn:  # transaction
            cur = conn.execute(
                "INSERT OR REPLACE INTO prep_submissions "
                "(prep_id, category_name, payload_json, created_at) "
                "VALUES (?, ?, ?, ?)",
                (prep_id, category_name, json.dumps(payload), now),
            )
            return int(cur.lastrowid or 0)
    finally:
        conn.close()


def _load_submissions_sync(prep_id: str) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT category_name, payload_json FROM prep_submissions "
            "WHERE prep_id = ? ORDER BY id ASC",
            (prep_id,),
        ).fetchall()
    finally:
        conn.close()
    out: list[dict] = []
    for name, payload_json in rows:
        try:
            out.append({"category_name": name, "payload": json.loads(payload_json)})
        except (ValueError, TypeError):
            continue
    return out


# --------------------------------------------------------------------------- #
# Public async API (never raises)
# --------------------------------------------------------------------------- #


async def save_prep(payload: dict, created_by: str | None = None) -> dict:
    """Persist a prepared-generation record. Returns {"content": {"prep_id": ...}}.

    Rejects an over-large payload (``QA_PREP_MAX_BYTES``) so a host cannot wedge
    the store with a pathological submission. Never raises.
    """
    try:
        blob = json.dumps(payload or {})
        if len(blob.encode("utf-8")) > _max_bytes():
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


async def load_prep(prep_id: str) -> dict:
    """Load a prep record by id. Returns {"content": dict|None}.

    Content is ``None`` for an unknown, tampered (uuid miss), or EXPIRED id (an
    expired record is deleted on this read). Never raises.
    """
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
        if prep_id:
            await asyncio.to_thread(_delete_prep_sync, prep_id)
        return {"error": None, "content": {"prep_id": prep_id}}
    except Exception as exc:
        logger.exception("prep_store.delete_prep failed")
        return {"error": str(exc), "content": None}


async def update_prep(prep_id: str, payload: dict) -> dict:
    """Overwrite an existing prep record's payload IN PLACE (host-mode gap loop).

    Used to persist the incremented gap-loop round in the envelope's ``meta`` so a
    host cannot ping-pong forever. Deliberately keeps ``created_at`` unchanged, so
    the TTL still counts from prepare and a looping host cannot extend it by
    resubmitting. Rejects an over-large payload and a missing/unknown/expired
    ``prep_id`` (the UPDATE simply matches no row). Never raises.
    """
    try:
        if not prep_id:
            return {"error": "prep_id is required", "content": None}
        blob = json.dumps(payload or {})
        if len(blob.encode("utf-8")) > _max_bytes():
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


async def save_submission(prep_id: str, category_name: str, payload: dict) -> dict:
    """Persist ONE per-category host submission (incremental submit path).

    Keyed UNIQUE per (prep_id, category_name) so a re-submitted category
    REPLACES the earlier one rather than duplicating it. Rejects an over-large
    payload and an unknown/expired ``prep_id`` (checked first for a clear error,
    ahead of the FK constraint). Never raises.
    """
    try:
        if not prep_id or not category_name:
            return {
                "error": "prep_id and category_name are required",
                "content": None,
            }
        blob = json.dumps(payload or {})
        if len(blob.encode("utf-8")) > _max_bytes():
            return {
                "error": f"submission payload exceeds {_max_bytes()} bytes",
                "content": None,
            }
        existing = await asyncio.to_thread(_load_prep_sync, prep_id)
        if existing is None:
            return {"error": "unknown or expired prep_id", "content": None}
        sub_id = await asyncio.to_thread(
            _save_submission_sync, prep_id, category_name, payload or {}
        )
        return {"error": None, "content": {"submission_id": sub_id}}
    except Exception as exc:
        logger.exception("prep_store.save_submission failed")
        return {"error": str(exc), "content": None}


async def load_submissions(prep_id: str) -> dict:
    """Load all accumulated per-category submissions for a prep_id (ordered)."""
    try:
        if not prep_id:
            return {"error": None, "content": []}
        rows = await asyncio.to_thread(_load_submissions_sync, prep_id)
        return {"error": None, "content": rows}
    except Exception as exc:
        logger.exception("prep_store.load_submissions failed")
        return {"error": str(exc), "content": None}
