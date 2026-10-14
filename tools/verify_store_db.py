"""SQLite plumbing for ``tools.verify_store`` (connect, save, load).

Split out of the facade so that module stays small. The facade owns ``_SCHEMA``,
``_db_path`` and ``_BUSY_TIMEOUT_S`` and passes them in, so tests that rebind
them on ``verify_store`` keep biting.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from tools import sqlite_store

logger = logging.getLogger("tools.verify_store")

# Databases created before 2026-09-01 predate site_checked. Added here rather
# than by a migration script: an additive column with a default is the whole
# change, and a failed ALTER on an already-migrated database is expected, not an
# error.
_MIGRATIONS = (
    "ALTER TABLE atlassian_verifications ADD COLUMN site_checked INTEGER NOT NULL DEFAULT 0",
)


class Verdict(NamedTuple):
    client: str
    verified: bool
    tool: str
    at_epoch: float
    site_checked: bool


def _migrate(conn: sqlite3.Connection) -> None:
    for statement in _MIGRATIONS:
        try:
            conn.execute(statement)
        except sqlite3.OperationalError:
            pass  # Already applied - the column exists.


def open_db(path: Path, schema: str, busy_timeout_s: float) -> sqlite3.Connection:
    conn = sqlite_store.connect(
        path,
        sqlite_store.ConnectOpts(
            timeout=busy_timeout_s,
            wal=True,
            busy_timeout_ms=int(busy_timeout_s * 1000),
            pragmas_best_effort=True,
            logger=logger,
            log_label="verify_store",
        ),
    )
    try:
        conn.executescript(schema)
        _migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def iso(epoch: float) -> str:
    try:
        return (
            datetime.fromtimestamp(float(epoch), tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat()
        )
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def save(conn: sqlite3.Connection, v: Verdict) -> None:
    with conn:
        conn.execute(
            "INSERT INTO atlassian_verifications "
            "(client, verified, at_epoch, tool, site_checked) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(client) DO UPDATE SET "
            "verified=excluded.verified, at_epoch=excluded.at_epoch, "
            "tool=excluded.tool, site_checked=excluded.site_checked",
            (
                v.client,
                1 if v.verified else 0,
                float(v.at_epoch),
                str(v.tool)[:120],
                1 if v.site_checked else 0,
            ),
        )


def load_row(conn: sqlite3.Connection, client: str) -> dict | None:
    row = conn.execute(
        "SELECT verified, at_epoch, tool, site_checked "
        "FROM atlassian_verifications WHERE client = ?",
        (client,),
    ).fetchone()
    if not row:
        return None
    return {
        "verified": bool(row[0]),
        "at": iso(row[1]),
        "tool": str(row[2] or ""),
        "site_checked": bool(row[3]),
        "age_s": max(0.0, time.time() - float(row[1])),
    }
