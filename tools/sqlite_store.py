"""Tiny sqlite helper shared by the api_* stores and the SQLite-backed stores."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ConnectOpts:
    """Options for :func:`connect` (every field optional)."""

    timeout: float | None = None
    wal: bool = False
    busy_timeout_ms: int | None = None
    pragmas_best_effort: bool = False
    foreign_keys: bool = False
    logger: logging.Logger | None = None
    log_label: str = ""
    row_factory: Any = None
    query_only: bool = False
    create_parent: bool = True


def _apply_wal_pragmas(conn: sqlite3.Connection, o: ConnectOpts) -> None:
    try:
        if o.wal:
            conn.execute("PRAGMA journal_mode=WAL")
        if o.busy_timeout_ms is not None:
            conn.execute(f"PRAGMA busy_timeout={int(o.busy_timeout_ms)}")
    except sqlite3.Error:
        if not o.pragmas_best_effort:
            raise
        if o.logger is not None:
            o.logger.debug(
                "%s: WAL/busy_timeout pragmas unavailable",
                o.log_label,
                exc_info=True,
            )


def _open(path: Path, timeout: float | None) -> sqlite3.Connection:
    """``sqlite3.connect`` with ``timeout`` passed only when given."""
    if timeout is None:
        return sqlite3.connect(str(path))
    return sqlite3.connect(str(path), timeout=timeout)


def _configure(conn: sqlite3.Connection, o: ConnectOpts) -> None:
    if o.row_factory is not None:
        conn.row_factory = o.row_factory
    if o.wal or o.busy_timeout_ms is not None:
        _apply_wal_pragmas(conn, o)
    if o.foreign_keys:
        conn.execute("PRAGMA foreign_keys=ON")
    if o.query_only:
        conn.execute("PRAGMA query_only=ON")


def connect(path: Path, opts: ConnectOpts | None = None) -> sqlite3.Connection:
    """Open *path* (creating its parent unless ``create_parent=False``) with
    explicit pragmas; ``row_factory`` is set right after open and ``query_only``
    runs ``PRAGMA query_only=ON`` last.

    ``timeout=None`` means the ``timeout`` kwarg is not passed (sqlite default).
    If a pragma step raises, the connection is closed before re-raising; once this
    returns, the caller owns it (and must close it if its own next step fails).
    """
    o = opts or ConnectOpts()
    if o.create_parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = _open(path, o.timeout)
    try:
        _configure(conn, o)
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
