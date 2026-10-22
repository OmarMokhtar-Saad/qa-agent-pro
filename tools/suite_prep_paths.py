"""Workbook paths recorded in finalized prep records, fetched in ONE query."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3

from tools import prep_store

logger = logging.getLogger("tools.mcp_handlers")


def _paths_sync(prep_ids: list) -> dict:
    marks = ",".join("?" * len(prep_ids))
    conn = prep_store._connect()
    try:
        sql = f"SELECT id, payload_json FROM preps WHERE id IN ({marks})"
        rows = conn.execute(sql, prep_ids).fetchall()
    finally:
        conn.close()
    return {pid: _export_path(raw) for pid, raw in rows}


def _export_path(raw: str) -> str:
    try:
        done = (json.loads(raw) or {}).get(prep_store.FINALIZED_KEY) or {}
    except (ValueError, TypeError, AttributeError):
        return ""
    return str(done.get("export_path") or "") if isinstance(done, dict) else ""


async def export_paths(prep_ids: list) -> dict:
    """{prep_id: export_path} for the given prep ids ({} on any failure)."""
    ids = sorted({p for p in prep_ids if p})
    if not ids:
        return {}
    try:
        return await asyncio.to_thread(_paths_sync, ids)
    except (sqlite3.Error, OSError):
        logger.warning("prep export path lookup failed", exc_info=True)
        return {}
