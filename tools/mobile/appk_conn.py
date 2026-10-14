"""Opening the knowledge store: schema, migration and the connection."""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

from tools import sqlite_store
from tools.mobile import appk_consts, appk_paths, knowledge_schema

logger = logging.getLogger("tools.mobile.app_knowledge")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    when_json TEXT NOT NULL,
    then_json TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    app_version TEXT NOT NULL DEFAULT '',
    source_run TEXT NOT NULL DEFAULT '',
    trust TEXT NOT NULL DEFAULT 'tester',
    status TEXT NOT NULL DEFAULT 'active',
    confirmed INTEGER NOT NULL DEFAULT 0,
    contradicted INTEGER NOT NULL DEFAULT 0,
    supersedes INTEGER,
    valid_from REAL NOT NULL,
    invalid_at REAL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    note_id INTEGER,
    event TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT ''
);
"""


def _migrate_once(conn: sqlite3.Connection) -> None:
    """Bring a write connection to schema v2 when the file is not there yet.
    A failure degrades like a v1 file: the slice-1 tables still work."""
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None or row[0] != knowledge_schema.SCHEMA_VERSION:
            knowledge_schema.migrate(conn)
    except (sqlite3.Error, ValueError, OSError):
        logger.info("app knowledge: schema migration skipped", exc_info=True)


def _create_store_files(path: Path) -> None:
    """Create the 0700 directory and the 0600 database file when missing."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.close(os.open(str(path), os.O_CREAT | os.O_WRONLY, 0o600))


def _init_schema(conn: sqlite3.Connection) -> None:
    """Create the tables, record the schema version and migrate once."""
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
        (appk_consts._SCHEMA_VERSION,),
    )
    # The INSERT opened an implicit transaction; close it, or the caller's
    # BEGIN IMMEDIATE raises "cannot start a transaction within a transaction".
    conn.commit()
    _migrate_once(conn)


# WRITE creates the directory (0700), the file (0600) and the schema. READ
# never creates anything (the caller checks the file exists first) and is
# ``query_only``. Both tighten an existing store (``tighten_store``).
# ``mode=ro`` is not used: it can fail on a WAL file. SQLite gives new
# ``-wal`` and ``-shm`` files the database file's mode.
def _connect(path: Path, *, write: bool) -> sqlite3.Connection:
    """Open the store: WRITE builds it, READ is ``query_only``."""
    if write:
        _create_store_files(path)
    appk_paths.tighten_store(path)
    conn = sqlite_store.connect(
        path,
        sqlite_store.ConnectOpts(
            timeout=appk_consts.DB_TIMEOUT_S,
            wal=write,
            query_only=not write,
            create_parent=False,
            row_factory=sqlite3.Row,
        ),
    )
    _init_or_close(conn, write)
    return conn


def _init_or_close(conn: sqlite3.Connection, write: bool) -> None:
    try:
        if write:
            _init_schema(conn)
    except BaseException:
        # The caller's closing() never sees a connection this raised out of.
        conn.close()
        raise
