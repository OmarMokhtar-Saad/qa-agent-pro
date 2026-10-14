"""Per-category prep submissions (leaf of ``tools.prep_store``)."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from tools import prep_store_crud as _crud
from tools import prep_store_touch as _touch
from tools.prep_store_cfg import _max_bytes, _payload_too_big, _touch_enabled
from tools.prep_store_conn import _connect

logger = logging.getLogger("tools.prep_store")


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


def _submission_refusal(prep_id: str, category_name: str, payload: dict) -> dict | None:
    if not prep_id or not category_name:
        return {
            "error": "prep_id and category_name are required",
            "content": None,
        }
    if _payload_too_big(payload):
        return {
            "error": f"submission payload exceeds {_max_bytes()} bytes",
            "content": None,
        }
    return None


# Keyed UNIQUE per (prep_id, category_name) so a re-submitted category
# REPLACES the earlier one rather than duplicating it. Rejects an over-large
# payload and an unknown/expired ``prep_id`` (checked first for a clear error,
# ahead of the FK constraint). Never raises.
#
# The touch below is real orchestration activity: restart the TTL clock so an
# active parallel fan-out cannot expire mid-run (2026-07-31 incident), and
# mark the prep as ACTIVE for the disclosure listing. Either flag alone is
# enough to want the timestamp -- see _touch_enabled.
#
# The crud workers are read through the module object so a patch of
# ``prep_store_crud._load_prep_sync`` / ``prep_store_touch._touch_prep_sync`` bites here.
async def save_submission(prep_id: str, category_name: str, payload: dict) -> dict:
    """Persist ONE per-category host submission (incremental submit path)."""
    try:
        refusal = _submission_refusal(prep_id, category_name, payload)
        if refusal is not None:
            return refusal
        existing = await asyncio.to_thread(_crud._load_prep_sync, prep_id)
        if existing is None:
            return {"error": "unknown or expired prep_id", "content": None}
        sub_id = await asyncio.to_thread(
            _save_submission_sync, prep_id, category_name, payload or {}
        )
        if _touch_enabled():
            await asyncio.to_thread(_touch._touch_prep_sync, prep_id)
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
