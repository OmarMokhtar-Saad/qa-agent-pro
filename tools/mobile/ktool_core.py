"""Shared constants and write plumbing of the ``qa_mobile_knowledge`` tool."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from tools.mobile import knowledge_db as kdb
from tools.mobile import knowledge_gate as gate
from tools.untrusted import wrap_untrusted

logger = logging.getLogger("tools.mobile.knowledge_tool")

ACTIONS = (
    "list",
    "show",
    "confirm",
    "reject",
    "edit",
    "resolve",
    "export",
    "import",
    "rollback",
    "reflect",
    "reflect_submit",
    "review",
)
TABLES = (
    "notes",
    "lessons",
    "shortcuts",
    "elements",
    "screens",
    "edges",
    "timings",
    "popups",
    "mistakes",
)
_WARN = "⚠️ "
_ROW_CHARS = 600
_SQL_ERRORS = (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, OSError)
_EDITABLE = {"notes": "text", "lessons": "text", "mistakes": "fix"}


def _refuse(message: str) -> str:
    return _WARN + message


def _target(table: str, item_id: Any):
    """``(table, number)`` from ``<table>:<id>`` or a bare number, else ``None``."""
    if isinstance(item_id, str) and ":" in item_id:
        return kdb.parse_item_id(item_id)
    try:
        number = int(item_id)
    except (TypeError, ValueError, OverflowError):
        return None
    return (table, number) if number > 0 and table in TABLES else None


def _rollback_quietly(conn) -> None:
    try:
        conn.rollback()
    except sqlite3.Error:
        pass


def _write(package: str, work) -> str:
    """Run ``work(conn)`` in one immediate transaction; its string is the reply."""
    conn = kdb.open_rw(package)
    if conn is None:
        return _refuse(
            "`package` is not an Android package name or the store is unavailable"
        )
    try:
        conn.execute("BEGIN IMMEDIATE")
        reply = work(conn)
        conn.commit()
        return reply
    except _SQL_ERRORS:
        logger.exception("qa_mobile_knowledge write failed")
        _rollback_quietly(conn)
        return _refuse("the knowledge store could not be changed")
    finally:
        conn.close()


def _row_of(conn, table: str, number: int):
    return conn.execute(
        "SELECT * FROM %s WHERE id = ? AND invalid_at IS NULL" % table, (number,)
    ).fetchone()


def _said(text: str) -> str:
    return wrap_untrusted("app_knowledge_reply", text, limit=2000)


def _clone(row, column: str, text: str) -> dict:
    values = {k: row[k] for k in row.keys() if k not in gate._KEEP_OUT}
    values[column] = text
    return values
