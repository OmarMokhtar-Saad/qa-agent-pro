"""Schema v2 of the per-app knowledge database, its migration and the rollback engine.

``migrate(conn)`` is called by ``app_knowledge._connect(write=True)`` after the
slice-1 schema exists. It is additive and idempotent: new tables are created
with ``IF NOT EXISTS``, columns of ``notes`` and ``events`` are added only when
``PRAGMA table_info`` says they are missing, then ``meta.schema_version`` is set.
Nothing is ever dropped, so a v1 server still reads a v2 file.

Learned rows are never deleted by learning: supersede closes the old row
(``invalid_at``, status ``superseded``) and inserts the new one. Every write
through ``knowledge_db`` emits one ``events`` row carrying ``before_json``;
``rollback_run`` replays a run's events in reverse to undo it.
"""

from __future__ import annotations

import logging
import sqlite3

from tools.mobile.knowledge_schema_ddl import (  # noqa: F401
    _COMMON,
    _EVENTS_ADD,
    _NOTES_ADD,
    _RUNS,
    _TABLES,
    LEARNED_TABLES,
    NATURAL_KEYS,
    columns,
)
from tools.mobile.knowledge_schema_undo import rollback_run  # noqa: F401

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "2"

_columns = columns


def _add_columns(conn: sqlite3.Connection, table: str, wanted: tuple) -> None:
    have = set(_columns(conn, table))
    for name, decl in wanted:
        if name not in have:
            conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, decl))


def _create_tables(conn: sqlite3.Connection) -> None:
    for name, own in _TABLES.items():
        conn.execute(
            "CREATE TABLE IF NOT EXISTS %s (id INTEGER PRIMARY KEY AUTOINCREMENT, %s, %s)"
            % (name, own, _COMMON)
        )
    conn.executescript(_RUNS)


def _create_indexes(conn: sqlite3.Connection) -> None:
    for table, key in NATURAL_KEYS.items():
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_%s_open ON %s (%s) WHERE invalid_at IS NULL"
            % (table, table, ", ".join(key))
        )
    for table in ("notes", "lessons", "elements", "timings", "mistakes"):
        conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_%s_screen ON %s (screen_key, status)"
            % (table, table)
        )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_edges_from ON edges (from_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_events_run ON events (run_id)")


def _stamp_version(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (SCHEMA_VERSION,),
    )


def migrate(conn: sqlite3.Connection) -> bool:
    """Bring *conn* to schema v2. Idempotent; never raises (False on failure)."""
    try:
        _add_columns(conn, "notes", _NOTES_ADD)
        _add_columns(conn, "events", _EVENTS_ADD)
        _create_tables(conn)
        _create_indexes(conn)
        _stamp_version(conn)
        conn.commit()
        return True
    except (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, OSError):
        logger.warning("knowledge schema: migration failed", exc_info=True)
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        return False


def table_columns(conn: sqlite3.Connection, table: str) -> list:
    """Column names of *table* (``[]`` for an unknown table)."""
    try:
        return _columns(conn, table)
    except sqlite3.Error:
        return []
