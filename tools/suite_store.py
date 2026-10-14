"""Suite persistence store — SQLite-backed, never-raises (T-01 / I-036 / B-022).

Generated suites are the primary artifact of the app, yet before this store a
browser refresh destroyed ~2 minutes of generation and 8-16 LLM calls, and made
every export unrecoverable. This module persists each suite (and its cases) to a
local SQLite file so they survive refreshes, stay re-exportable, and can be
listed as "recent suites" on chat start.

Contract (never-raises), mirroring tools/rag_store.py:
  On success: {"error": None, "content": <value>}
  On failure: {"error": str, "content": None}

All blocking sqlite I/O is wrapped in asyncio.to_thread(). A corrupt or
unwritable DB degrades gracefully to an error result (callers fall back to the
in-memory session copy) instead of raising.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tools import (
    settings_port,
    suite_store_api,
    suite_store_db,
    suite_store_load,
    suite_store_write,
)
from tools.install_paths import resolve_data_path
from tools.models import TestSuite
from tools.suite_store_db import ensure_columns as _ensure_columns  # noqa: F401
from tools.suite_store_write import SuiteMeta

logger = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS suites (
    id           TEXT PRIMARY KEY,
    feature_text TEXT NOT NULL DEFAULT '',
    source_url   TEXT,
    created_at   REAL NOT NULL,
    created_by   TEXT,
    prep_id      TEXT,
    provenance_json TEXT
);
CREATE TABLE IF NOT EXISTS cases (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    suite_id     TEXT NOT NULL,
    stable_id    TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1,
    updated_at   REAL NOT NULL,
    FOREIGN KEY (suite_id) REFERENCES suites(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_cases_suite_id ON cases(suite_id);
CREATE TABLE IF NOT EXISTS checklists (
    suite_id     TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    created_at   REAL NOT NULL,
    FOREIGN KEY (suite_id) REFERENCES suites(id) ON DELETE CASCADE
);
"""


def db_path() -> Path:
    # Install-root anchored -- see tools/install_paths. A cwd-relative store meant
    # qa_export_suite could not find a suite that plainly existed.
    return resolve_data_path(settings_port.get().qa_suite_store_path)


def connect() -> sqlite3.Connection:
    """Open the DB (creating parent dirs + schema). Caller must close."""
    return suite_store_db.open_db(_db_path(), _SCHEMA)


def _run(fn: Callable[..., Any], *args: Any) -> Any:
    """Open a connection, call ``fn(conn, *args)``, always close it."""
    conn = _connect()
    try:
        return fn(conn, *args)
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Sync workers (run inside asyncio.to_thread)
# --------------------------------------------------------------------------- #


def _save_suite_sync(suite: TestSuite, meta: SuiteMeta) -> str:
    return _run(suite_store_write.save, suite, meta)


def _load_suite_sync(suite_id: str) -> TestSuite | None:
    return _run(suite_store_load.load_suite, suite_id)


# --------------------------------------------------------------------------- #
# Public async API (never raises)
# --------------------------------------------------------------------------- #


# *prep_id* (host mode) makes a RETRIED finalize idempotent: a second save for
# the same prep UPDATES the suite row that prep already produced and returns
# THAT row's id, instead of leaving two suites behind for one generation. The
# guarantee is a partial UNIQUE index plus a single upsert statement, so it
# holds even when the two finalizes run CONCURRENTLY. Omitted (the default)
# the behaviour is byte-identical to before.
#
# *provenance* (v1.97.0 cursor-hardening item 3b) is stored as JSON;
# serialization failures store None rather than raising.
async def save_suite(suite: TestSuite, meta: SuiteMeta | None = None) -> dict:
    """Persist a suite and its cases. Returns {"content": {"suite_id": ...}}."""
    return await suite_store_api.save_suite(_run, suite, meta or SuiteMeta())


async def load_suite(suite_id: str) -> dict:
    """Load a suite by id. Returns {"content": TestSuite|None} (None if unknown)."""
    return await suite_store_api.load_suite(_run, suite_id)


# ``load_suite`` rebuilds the TestSuite from the persisted cases; this returns
# the suite ROW instead (feature_text / source_url / created_at / created_by).
# It was added for tools/zephyr_exporter.py, whose Project / Issue columns
# came from the Jira story key. That module and its caller
# ``mcp_handlers._suite_story_key`` were DELETED on 2026-08-15 (dead-code
# deletion batch D4), so this accessor has NO production caller today. It
# is RETAINED on purpose -- a general-purpose store read on a live module,
# not a zephyr helper -- and tests/test_suite_store.py is its cover. No
# schema change: every column already existed. Never raises.
async def load_suite_meta(suite_id: str) -> dict:
    """Load a suite's stored ROW metadata by id. Returns {"content": dict|None}."""
    return await suite_store_api.load_suite_meta(_run, suite_id)


async def list_recent_suites(limit: int = 5) -> dict:
    """Return up to `limit` most-recently-created suites (metadata only)."""
    return await suite_store_api.list_recent_suites(_run, limit)


async def find_recent_suite_by_source(
    source_url: str, window_s: float | None = None
) -> dict:
    """Most recent suite saved for `source_url`, or None. `window_s=None` or
    `<=0` means no age limit (item 3a's identity check); a positive value
    bounds it (item 8's 24h warning). Never raises."""
    return await suite_store_api.find_recent_suite_by_source(_run, source_url, window_s)


# --------------------------------------------------------------------------- #
# Atomic Requirements Checklist persistence (Batch 2)
#
# The checklist + its coverage audit are a DURABLE artifact, not an in-memory
# intermediate: a coverage claim must stay re-auditable after the session ends.
# --------------------------------------------------------------------------- #


async def save_checklist(suite_id: str, payload: dict) -> dict:
    """Persist a suite's atomic checklist + coverage audit. Never raises."""
    return await suite_store_api.save_checklist(_run, suite_id, payload)


async def load_checklist(suite_id: str) -> dict:
    """Load a suite's persisted checklist payload. Returns
    ``{"content": dict|None}`` (None when unknown). Never raises."""
    return await suite_store_api.load_checklist(_run, suite_id)


def _connect() -> sqlite3.Connection:
    """Legacy name of :func:`connect` (WX removes it)."""
    return connect()


def _db_path() -> Path:
    """Legacy name of :func:`db_path` (WX removes it)."""
    return db_path()
