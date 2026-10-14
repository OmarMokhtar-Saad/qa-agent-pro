"""Shared primitives of the knowledge db write path: constants, events, refusals."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any

from tools.mobile import app_knowledge, appk_consts, knowledge_limits
from tools.mobile.knowledge_db_args import Ev, Refusal

logger = logging.getLogger("tools.mobile.knowledge_db")

STATUSES = (
    "candidate",
    "active",
    "stale",
    "disputed",
    "needs_recheck",
    "superseded",
    "retired",
)
CLOSED = ("superseded", "retired")
#: Columns holding ids, keys and hashes, not prose: never scanned for secrets.
_STRUCTURAL = frozenset(
    {
        "source_run",
        "app_version",
        "trust",
        "status",
        "runs_json",
        "screen_key",
        "from_key",
        "to_key",
        "start_key",
        "end_key",
        "fp_hash",
        "element_fp",
        "fp_json",
        "anchors_json",
        "samples_json",
        "last_used_run",
        "created",
        "valid_from",
        "invalid_at",
        "recheck_at",
        "missing_at",
        "run_id",
        "version_code",
        "learn_state",
        "reflect_state",
    }
)
_DETAIL_CHARS = 200
NO_WRITE = frozenset({"id"})


#: Failures a helper turns into a safe default; anything else is a bug.
ERRORS = (
    sqlite3.Error,
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
    OSError,
    OverflowError,
)


def dump(value: Any) -> Any:
    # allow_nan=False: NaN/Infinity would be stored as non-strict JSON (ValueError).
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, allow_nan=False)
    return value


def secret(values: dict, run_id: str) -> str:
    """The refusal reason for the first prose string in *values*, or ``""``."""
    for col, value in values.items():
        if col in _STRUCTURAL:
            continue
        for leaf in app_knowledge._leaf_strings(value):
            reason = app_knowledge.secret_reason(leaf, (), run_id)
            if reason:
                return "%s: %s" % (col, reason)
    return ""


def cap(table: str) -> int:
    if table == "notes":
        return appk_consts.MAX_NOTE_ROWS
    return knowledge_limits.MAX_ROWS.get(table, 0)


def count(conn, table: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]


def owns(conn) -> bool:
    try:
        return not conn.in_transaction
    except sqlite3.Error:
        return True


def safe_rollback(conn, owns: bool) -> None:
    if owns:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass


def finish(conn, owns: bool, ok: bool) -> None:
    if not owns:
        return
    if ok:
        conn.commit()
    else:
        conn.rollback()


def event(conn, name: str, ev: Ev) -> None:
    conn.execute(
        "INSERT INTO events (ts, note_id, event, detail, run_id, table_name, row_id, before_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            time.time(),
            ev.row_id if ev.table == "notes" else None,
            name,
            str(ev.why or "")[:_DETAIL_CHARS],
            str(ev.run_id or "")[:64],
            ev.table,
            ev.row_id,
            json.dumps(ev.before, sort_keys=True) if ev.before else "",
        ),
    )


def refuse(conn, table: str, r: Refusal) -> None:
    try:
        event(
            conn,
            "refused",
            Ev(table, None, r.run_id, "%s (%s)" % (r.reason, r.why), None),
        )
        # A bulk import logs one refusal per row: keep only the newest.
        conn.execute(
            "DELETE FROM events WHERE id IN (SELECT id FROM events WHERE event = 'refused'"
            " ORDER BY id DESC LIMIT -1 OFFSET ?)",
            (knowledge_limits.MAX_REFUSED_EVENTS,),
        )
    except sqlite3.Error:
        logger.info("knowledge db: could not log a refusal")
