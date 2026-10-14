"""Prep touch (leaf of ``tools.prep_store``)."""

from __future__ import annotations

import asyncio
import logging
import time

from tools.prep_store_cfg import _touch_enabled
from tools.prep_store_conn import _connect, _has_touched_at

logger = logging.getLogger("tools.prep_store")


def _touch_prep_sync(prep_id: str) -> bool:
    now = time.time()
    conn = _connect()
    try:
        if not _has_touched_at(conn):
            # Migration deferred (locked DB): skip silently, the caller keeps
            # the fixed TTL rather than failing the orchestration step.
            return False
        with conn:  # transaction
            cur = conn.execute(
                "UPDATE preps SET touched_at = ? WHERE id = ?", (now, prep_id)
            )
        return (cur.rowcount or 0) > 0
    finally:
        conn.close()


# Unconditional since 2026-08-12 (see _touch_enabled: the disclosure needs
# the touch to see a fetched-packet-only run, which is exactly the
# 2026-07-31 incident shape). The TTL clock slides on every touch and total
# lifetime stays bounded by QA_PREP_MAX_LIFETIME_S (see _expired). Never
# raises.
async def touch_prep(prep_id: str) -> dict:
    """Record real orchestration activity (qa_get_category_job /
    qa_submit_category) on a prep."""
    try:
        if not prep_id or not _touch_enabled():
            return {"error": None, "content": None}
        ok = await asyncio.to_thread(_touch_prep_sync, prep_id)
        return {"error": None, "content": {"touched": bool(ok)}}
    except Exception as exc:
        logger.exception("prep_store.touch_prep failed")
        return {"error": str(exc), "content": None}
