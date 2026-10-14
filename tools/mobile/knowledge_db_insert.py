"""The insert path of the knowledge db: row defaults, refusals, cap, event."""

from __future__ import annotations

import json
import logging
import sqlite3
import time

from tools.mobile import knowledge_schema
from tools.mobile.knowledge_db_args import Ev, Refusal, Write
from tools.mobile.knowledge_db_compact import compact
from tools.mobile.knowledge_db_core import (
    ERRORS,
    NO_WRITE,
    cap,
    count,
    dump,
    event,
    finish,
    owns,
    refuse,
    safe_rollback,
    secret,
)

logger = logging.getLogger("tools.mobile.knowledge_db")


def _row(table: str, cols: list, w: Write) -> dict:
    now = time.time()
    defaults = {
        "created": now,
        "valid_from": now,
        "source_run": w.run_id,
        "trust": "learned",
        "status": "candidate",
        "runs_json": json.dumps([w.run_id] if w.run_id else []),
    }
    row = {k: v for k, v in defaults.items() if k in cols}
    row.update(
        {k: dump(v) for k, v in w.values.items() if k in cols and k not in NO_WRITE}
    )
    return row


def _prepare(conn, table: str, w: Write) -> tuple:
    """``(row, "")`` or ``(None, reason)``."""
    if table not in knowledge_schema.LEARNED_TABLES and table != "runs":
        return None, "unknown table"
    cols = knowledge_schema.table_columns(conn, table)
    if not cols:
        return None, "table missing"
    try:
        row = _row(table, cols, w)
    except ValueError:
        return None, "non-finite number in a json value"
    if not row:
        return None, "no known columns"
    reason = secret(row, w.run_id)
    return (None, "secret " + reason) if reason else (row, "")


def full(conn, table: str) -> bool:
    limit = cap(table)
    if not limit or count(conn, table) < limit:
        return False
    compact(conn, table)
    return count(conn, table) >= limit


def insert_row(conn, table: str, w: Write) -> tuple:
    """``(row_id, "")`` or ``(None, reason)``. Writes no event."""
    row, reason = _prepare(conn, table, w)
    if row is None:
        return None, reason
    if full(conn, table):
        return None, "cap: %s is full" % table
    names = list(row)
    cur = conn.execute(
        "INSERT INTO %s (%s) VALUES (%s)"
        % (table, ", ".join(names), ", ".join("?" * len(names))),
        [row[n] for n in names],
    )
    return cur.lastrowid, ""


def insert(conn, table: str, w: Write):
    """Insert one row (defaults, secret refusal, cap, event). Row id or ``None``."""
    own = owns(conn)
    try:
        clean = Write(w.values or {}, str(w.run_id or ""), w.why)
        row_id, reason = insert_row(conn, table, clean)
        if row_id is None:
            refuse(conn, table, Refusal(w.run_id, w.why, reason))
            finish(conn, own, True)
            return None
        event(conn, "insert", Ev(table, row_id, w.run_id, w.why, None))
        finish(conn, own, True)
        return row_id
    except sqlite3.IntegrityError:
        finish(conn, own, False)
        return None
    except ERRORS:
        logger.info("knowledge db: insert failed", exc_info=True)
        safe_rollback(conn, own)
        return None
