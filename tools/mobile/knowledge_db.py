"""The ONLY write path into the per-app knowledge database (schema v2).

Every helper: never raises; refuses a secret-looking string (``refused`` event,
``None`` back); checks the per-table row cap (compacting once before refusing);
emits exactly one ``events`` row carrying ``before_json`` so a run can be rolled
back (``knowledge_schema.rollback_run``). Rows are never deleted by learning:
``supersede`` closes the old row and inserts the new one.

Transactions: when the caller already opened one (``conn.in_transaction``) the
helper leaves commit/rollback to it; otherwise it commits its own work.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from tools.mobile import (
    app_knowledge,
    knowledge_limits,
    knowledge_schema,
    knowledge_scope,
)
from tools.mobile.knowledge_db_args import (  # noqa: F401
    Counter,
    Ev,
    Hit,
    Replace,
    Status,
    Where,
    Write,
)
from tools.mobile.knowledge_db_compact import compact  # noqa: F401
from tools.mobile.knowledge_db_core import (  # noqa: F401
    ERRORS,
    STATUSES,
    event,
    finish,
    owns,
    safe_rollback,
)
from tools.mobile.knowledge_db_core import cap as _cap  # noqa: F401
from tools.mobile.knowledge_db_counter import bump_runs as _bump_runs  # noqa: F401
from tools.mobile.knowledge_db_counter import upsert_counter  # noqa: F401
from tools.mobile.knowledge_db_insert import insert  # noqa: F401
from tools.mobile.knowledge_db_status import set_status  # noqa: F401
from tools.mobile.knowledge_db_supersede import supersede  # noqa: F401

logger = logging.getLogger(__name__)


def open_rw(package: Any):
    """A write connection to the app's knowledge db, or ``None``. Never raises."""
    try:
        path = app_knowledge._store_path(package)
        if path is None or not knowledge_scope.allowed(package):
            return None
        return app_knowledge._connect(path, write=True)
    except ERRORS:
        logger.info("knowledge db: could not open for write", exc_info=True)
        return None


def parse_item_id(item_id: Any):
    """``"notes:3"`` -> ``("notes", 3)``; ``None`` for anything else."""
    try:
        table, _, num = str(item_id or "").partition(":")
        if table not in knowledge_schema.LEARNED_TABLES or not num.isdigit():
            return None
        return table, int(num)
    except ERRORS:
        return None


def _sql(table: str, w: Where) -> tuple:
    clause = str(w.clause or "").strip()
    if clause.upper().startswith("WHERE "):
        clause = clause[6:]
    cap = max(1, min(int(w.limit), knowledge_limits.SNAPSHOT_MAX_ROWS))
    order = "rowid" if table == "runs" else "id"
    sql = "SELECT * FROM %s%s ORDER BY %s DESC LIMIT ?" % (
        table,
        " WHERE " + clause if clause else "",
        order,
    )
    return sql, list(w.args or ()) + [cap]


def rows(conn, table: str, where: Where | None = None) -> list:
    """Rows of *table* as dicts, newest first. *where* is a constant SQL fragment
    with ``?`` placeholders (never caller text); its args are bound. Bounded."""
    try:
        if table not in knowledge_schema.LEARNED_TABLES and table != "runs":
            return []
        sql, args = _sql(table, where if where is not None else Where())
        return [dict(row) for row in conn.execute(sql, args).fetchall()]
    except ERRORS:
        logger.info("knowledge db: rows failed", exc_info=True)
        return []


def has_marker(conn, run_id: str, stage: str) -> bool:
    """True when *stage* already learned *run_id* (a ``learn_marker`` event)."""
    try:
        return (
            conn.execute(
                "SELECT 1 FROM events WHERE event = 'learn_marker' AND run_id = ? AND detail = ? LIMIT 1",
                (str(run_id), str(stage)),
            ).fetchone()
            is not None
        )
    except sqlite3.Error:
        return False


def put_marker(conn, run_id: str, stage: str) -> bool:
    """Record that *stage* finished for *run_id*. Idempotent; never raises."""
    own = owns(conn)
    try:
        if not has_marker(conn, run_id, stage):
            event(conn, "learn_marker", Ev("", None, run_id, stage, None))
        finish(conn, own, True)
        return True
    except ERRORS:
        logger.info("knowledge db: put_marker failed", exc_info=True)
        safe_rollback(conn, own)
        return False
