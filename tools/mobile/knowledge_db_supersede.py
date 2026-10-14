"""Supersede (close a row and insert its replacement) of the knowledge db."""

from __future__ import annotations

import logging
import time

from tools.mobile import knowledge_schema
from tools.mobile.knowledge_db_args import Ev, Hit, Refusal, Replace, Write
from tools.mobile.knowledge_db_core import (
    ERRORS,
    event,
    finish,
    owns,
    refuse,
    safe_rollback,
)
from tools.mobile.knowledge_db_insert import insert_row

logger = logging.getLogger("tools.mobile.knowledge_db")


def _load_open(conn, table: str, old_id: int):
    return conn.execute(
        "SELECT * FROM %s WHERE id = ? AND invalid_at IS NULL" % table, (old_id,)
    ).fetchone()


def _before(old) -> dict:
    return {
        "old": {
            "id": old["id"],
            "status": old["status"],
            "invalid_at": old["invalid_at"],
        }
    }


def _replace(conn, hit: Hit, rep: Replace) -> tuple:
    """``(new_id, "")`` or ``(None, reason)``. Owns the savepoint: rolls back and
    releases it on failure; on success writes the event BEFORE the RELEASE. Never
    calls finish (only ``supersede`` does)."""
    conn.execute("SAVEPOINT ak_supersede")
    now = time.time()
    conn.execute(
        "UPDATE %s SET status = 'superseded', invalid_at = ? WHERE id = ?" % hit.table,
        (now, rep.old_id),
    )
    values = {**dict(rep.values or {}), "supersedes": rep.old_id, "valid_from": now}
    new_id, reason = insert_row(conn, hit.table, Write(values, rep.run_id))
    if new_id is None:
        conn.execute("ROLLBACK TO ak_supersede")
        conn.execute("RELEASE ak_supersede")
        return None, reason
    ev = Ev(hit.table, new_id, rep.run_id, rep.why, _before(hit.row))
    event(conn, "supersede", ev)
    conn.execute("RELEASE ak_supersede")
    return new_id, ""


def supersede(conn, table: str, rep: Replace):
    """Close row *old_id* and insert its replacement. New row id or ``None``."""
    own = owns(conn)
    try:
        rep = rep._replace(run_id=str(rep.run_id or ""))
        if table not in knowledge_schema.LEARNED_TABLES:
            return None
        old = _load_open(conn, table, rep.old_id)
        if old is None:
            finish(conn, own, True)
            return None
        new_id, reason = _replace(conn, Hit(table, old), rep)
        if new_id is None:
            refuse(conn, table, Refusal(rep.run_id, rep.why, reason))
        finish(conn, own, True)
        return new_id
    except ERRORS:
        logger.info("knowledge db: supersede failed", exc_info=True)
        safe_rollback(conn, own)
        return None
