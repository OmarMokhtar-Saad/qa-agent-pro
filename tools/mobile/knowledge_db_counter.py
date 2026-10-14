"""Counter upserts of the knowledge db."""

from __future__ import annotations

import json
import logging
from typing import Any

from tools.mobile import knowledge_limits, knowledge_schema
from tools.mobile.knowledge_db_args import Counter, Ev, Hit, Write
from tools.mobile.knowledge_db_core import (
    ERRORS,
    NO_WRITE,
    dump,
    event,
    finish,
    owns,
    safe_rollback,
)
from tools.mobile.knowledge_db_insert import insert

logger = logging.getLogger("tools.mobile.knowledge_db")


def _open_row(conn, table: str, key: dict):
    cols = set(knowledge_schema.table_columns(conn, table))
    if not key or not set(key) <= cols:
        return None
    where = " AND ".join("%s = ?" % k for k in key)
    return conn.execute(
        "SELECT * FROM %s WHERE %s AND invalid_at IS NULL ORDER BY id DESC LIMIT 1"
        % (table, where),
        [dump(v) for v in key.values()],
    ).fetchone()


def bump_runs(raw: Any, run_id: str) -> str:
    try:
        runs = [r for r in json.loads(str(raw or "[]")) if isinstance(r, str)]
    except ValueError:
        runs = []
    if run_id and run_id not in runs:
        runs.append(run_id)
    return json.dumps(runs[-knowledge_limits.MAX_RUNS_PER_ITEM :])


def _clean_deltas(conn, table: str, deltas: dict) -> tuple:
    """``(cols, deltas)`` or ``(cols, None)`` when the table or a delta is invalid."""
    cols = set(knowledge_schema.table_columns(conn, table))
    deltas = {
        k: v for k, v in (deltas or {}).items() if k in cols and k not in NO_WRITE
    }
    numeric = all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in deltas.values()
    )
    ok = table in knowledge_schema.LEARNED_TABLES and numeric
    return cols, (deltas if ok else None)


def _clean(conn, table: str, c: Counter) -> tuple:
    cols, deltas = _clean_deltas(conn, table, c.deltas)
    if deltas is None:
        return cols, None
    return cols, c._replace(deltas=deltas, run_id=str(c.run_id or ""))


def _bump(conn, hit: Hit, c: Counter) -> None:
    old = hit.row
    deltas = c.deltas
    before = {k: old[k] for k in list(deltas) + ["runs_json"] if k in hit.cols}
    sets = ["%s = COALESCE(%s, 0) + ?" % (k, k) for k in deltas]
    args: list = list(deltas.values())
    if "runs_json" in hit.cols:
        sets.append("runs_json = ?")
        args.append(bump_runs(old["runs_json"], c.run_id))
    if sets:
        conn.execute(
            "UPDATE %s SET %s WHERE id = ?" % (hit.table, ", ".join(sets)),
            args + [old["id"]],
        )
    event(conn, "counter", Ev(hit.table, old["id"], c.run_id, c.why, before))


def upsert_counter(conn, table: str, c: Counter):
    """Add *deltas* to the open row at natural *key*, or insert it. Row id or ``None``."""
    own = owns(conn)
    try:
        cols, c = _clean(conn, table, c)
        if c is None:
            finish(conn, own, True)
            return None
        old = _open_row(conn, table, c.key)
        if old is None:
            return insert(conn, table, Write({**c.key, **c.deltas}, c.run_id, c.why))
        _bump(conn, Hit(table, old, frozenset(cols)), c)
        finish(conn, own, True)
        return old["id"]
    except ERRORS:
        logger.info("knowledge db: upsert_counter failed", exc_info=True)
        safe_rollback(conn, own)
        return None
