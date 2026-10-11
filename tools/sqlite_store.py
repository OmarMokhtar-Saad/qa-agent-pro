"""Tiny sqlite helper shared by the api_* stores and the SQLite-backed stores."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path


def _apply_wal_pragmas(
    conn: sqlite3.Connection,
    *,
    wal: bool,
    busy_timeout_ms: int | None,
    best_effort: bool,
    logger: logging.Logger | None,
    log_label: str,
) -> None:
    try:
        if wal:
            conn.execute("PRAGMA journal_mode=WAL")
        if busy_timeout_ms is not None:
            conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    except sqlite3.Error:
        if not best_effort:
            raise
        if logger is not None:
            logger.debug(
                "%s: WAL/busy_timeout pragmas unavailable", log_label, exc_info=True
            )


def connect(
    path: Path,
    *,
    timeout: float | None = None,
    wal: bool = False,
    busy_timeout_ms: int | None = None,
    pragmas_best_effort: bool = False,
    foreign_keys: bool = False,
    logger: logging.Logger | None = None,
    log_label: str = "",
) -> sqlite3.Connection:
    """Open *path* (creating its parent) with explicit pragmas.

    ``timeout=None`` means the ``timeout`` kwarg is not passed (sqlite default).
    If a pragma step raises, the connection is closed before re-raising; once this
    returns, the caller owns it (and must close it if its own next step fails).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if timeout is None:
        conn = sqlite3.connect(str(path))
    else:
        conn = sqlite3.connect(str(path), timeout=timeout)
    try:
        if wal or busy_timeout_ms is not None:
            _apply_wal_pragmas(
                conn,
                wal=wal,
                busy_timeout_ms=busy_timeout_ms,
                best_effort=pragmas_best_effort,
                logger=logger,
                log_label=log_label,
            )
        if foreign_keys:
            conn.execute("PRAGMA foreign_keys=ON")
    except BaseException:
        conn.close()
        raise
    return conn


def open_schema_db(
    path: Path, schema: str, ensure_columns: Callable[[sqlite3.Connection], None]
) -> sqlite3.Connection:
    """Open *path* (creating its parent), apply *schema*, run the column migration."""
    conn = connect(path)
    try:
        conn.executescript(schema)
        ensure_columns(conn)
    except BaseException:
        conn.close()
        raise
    return conn
