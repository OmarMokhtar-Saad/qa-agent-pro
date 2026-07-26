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

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path

from config.settings import settings
from tools.models import TestCase, TestSuite

logger = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS suites (
    id           TEXT PRIMARY KEY,
    feature_text TEXT NOT NULL DEFAULT '',
    source_url   TEXT,
    created_at   REAL NOT NULL,
    created_by   TEXT
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
CREATE TABLE IF NOT EXISTS web_runs (
    id           TEXT PRIMARY KEY,
    suite_id     TEXT NOT NULL,
    base_url     TEXT NOT NULL DEFAULT '',
    dry_run      INTEGER NOT NULL DEFAULT 1,
    passed       INTEGER NOT NULL DEFAULT 0,
    failed       INTEGER NOT NULL DEFAULT 0,
    total        INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    created_at   REAL NOT NULL,
    FOREIGN KEY (suite_id) REFERENCES suites(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_web_runs_suite_id ON web_runs(suite_id);
"""


def _db_path() -> Path:
    return Path(settings.qa_suite_store_path)


def _connect() -> sqlite3.Connection:
    """Open the DB (creating parent dirs + schema). Caller must close."""
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    # Enforce declared FOREIGN KEY ... ON DELETE CASCADE (NB-019). SQLite defaults
    # this OFF per-connection, so the schema's cascade is inert without it — a
    # future suite-delete path would otherwise leave orphaned `cases` rows.
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    return conn


# --------------------------------------------------------------------------- #
# Sync workers (run inside asyncio.to_thread)
# --------------------------------------------------------------------------- #


def _save_suite_sync(
    suite: TestSuite,
    feature_text: str,
    source_url: str | None,
    created_by: str | None,
) -> str:
    now = time.time()
    conn = _connect()
    try:
        with conn:  # transaction
            conn.execute(
                "INSERT OR REPLACE INTO suites (id, feature_text, source_url, created_at, created_by) "
                "VALUES (?, ?, ?, ?, ?)",
                (suite.suite_id, feature_text or "", source_url, now, created_by),
            )
            # Replace the suite's cases wholesale so a re-save is idempotent.
            conn.execute("DELETE FROM cases WHERE suite_id = ?", (suite.suite_id,))
            conn.executemany(
                "INSERT INTO cases (suite_id, stable_id, payload_json, version, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        suite.suite_id,
                        tc.stable_id,
                        tc.model_dump_json(),
                        1,
                        now,
                    )
                    for tc in suite.test_cases
                ],
            )
        return suite.suite_id
    finally:
        conn.close()


def _load_suite_sync(suite_id: str) -> TestSuite | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT id FROM suites WHERE id = ?", (suite_id,)).fetchone()
        if row is None:
            return None
        case_rows = conn.execute(
            "SELECT payload_json FROM cases WHERE suite_id = ? ORDER BY id ASC",
            (suite_id,),
        ).fetchall()
    finally:
        conn.close()
    if not case_rows:
        return None
    cases = [TestCase.model_validate_json(r[0]) for r in case_rows]
    return TestSuite(suite_id=suite_id, test_cases=cases)


def _list_recent_sync(limit: int) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT s.id, s.feature_text, s.source_url, s.created_at, "
            "(SELECT COUNT(*) FROM cases c WHERE c.suite_id = s.id) AS case_count "
            "FROM suites s ORDER BY s.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "suite_id": r[0],
            "feature_text": r[1],
            "source_url": r[2],
            "created_at": r[3],
            "case_count": r[4],
        }
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# Public async API (never raises)
# --------------------------------------------------------------------------- #


async def save_suite(
    suite: TestSuite,
    feature_text: str = "",
    source_url: str | None = None,
    created_by: str | None = None,
) -> dict:
    """Persist a suite and its cases. Returns {"content": {"suite_id": ...}}."""
    try:
        suite_id = await asyncio.to_thread(
            _save_suite_sync, suite, feature_text, source_url, created_by
        )
        logger.info(
            "suite_store: saved suite %s (%d cases)", suite_id, len(suite.test_cases)
        )
        return {"error": None, "content": {"suite_id": suite_id}}
    except Exception as exc:
        logger.exception("suite_store.save_suite failed")
        return {"error": str(exc), "content": None}


async def load_suite(suite_id: str) -> dict:
    """Load a suite by id. Returns {"content": TestSuite|None} (None if unknown)."""
    try:
        if not suite_id:
            return {"error": None, "content": None}
        suite = await asyncio.to_thread(_load_suite_sync, suite_id)
        return {"error": None, "content": suite}
    except Exception as exc:
        logger.exception("suite_store.load_suite failed")
        return {"error": str(exc), "content": None}


async def list_recent_suites(limit: int = 5) -> dict:
    """Return up to `limit` most-recently-created suites (metadata only)."""
    try:
        rows = await asyncio.to_thread(_list_recent_sync, max(1, int(limit)))
        return {"error": None, "content": rows}
    except Exception as exc:
        logger.exception("suite_store.list_recent_suites failed")
        return {"error": str(exc), "content": None}


# --------------------------------------------------------------------------- #
# Web-run persistence (Web Suite Execution -- tools/web_runner.py)
# --------------------------------------------------------------------------- #


def _save_web_run_sync(
    suite_id: str,
    base_url: str,
    dry_run: bool,
    summary: dict,
    payload: dict,
) -> str:
    run_id = uuid.uuid4().hex
    now = time.time()
    conn = _connect()
    try:
        with conn:  # transaction
            # M1-web: web_runs.suite_id is an FK into suites and PRAGMA
            # foreign_keys is ON, so a run against an in-session suite that was
            # never persisted would raise IntegrityError and be silently dropped.
            # Upsert a stub suites row (INSERT OR IGNORE never touches an existing
            # suite's feature_text) so the self-contained run always persists.
            if suite_id:
                conn.execute(
                    "INSERT OR IGNORE INTO suites (id, feature_text, created_at) "
                    "VALUES (?, '', ?)",
                    (suite_id, now),
                )
            conn.execute(
                "INSERT INTO web_runs (id, suite_id, base_url, dry_run, passed, "
                "failed, total, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    suite_id or "",
                    base_url or "",
                    1 if dry_run else 0,
                    int(summary.get("passed", 0)),
                    int(summary.get("failed", 0)),
                    int(summary.get("total", 0)),
                    json.dumps(payload),
                    now,
                ),
            )
        return run_id
    finally:
        conn.close()


def _load_web_run_sync(run_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, suite_id, base_url, dry_run, passed, failed, total, "
            "payload_json, created_at FROM web_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    try:
        payload = json.loads(row[7])
    except (ValueError, TypeError):
        payload = {}
    return {
        "run_id": row[0],
        "suite_id": row[1],
        "base_url": row[2],
        "dry_run": bool(row[3]),
        "passed": row[4],
        "failed": row[5],
        "total": row[6],
        "payload": payload,
        "created_at": row[8],
    }


async def save_web_run(
    suite_id: str,
    base_url: str,
    dry_run: bool,
    summary: dict,
    payload: dict,
) -> dict:
    """Persist one web run. Returns {"content": {"run_id": ...}}. Never raises."""
    try:
        run_id = await asyncio.to_thread(
            _save_web_run_sync, suite_id, base_url, dry_run, summary, payload
        )
        logger.info("suite_store: saved web run %s (suite %s)", run_id, suite_id)
        return {"error": None, "content": {"run_id": run_id}}
    except Exception as exc:
        logger.exception("suite_store.save_web_run failed")
        return {"error": str(exc), "content": None}


async def load_web_run(run_id: str) -> dict:
    """Load a web run by id. Returns {"content": dict|None}. Never raises."""
    try:
        if not run_id:
            return {"error": None, "content": None}
        row = await asyncio.to_thread(_load_web_run_sync, run_id)
        return {"error": None, "content": row}
    except Exception as exc:
        logger.exception("suite_store.load_web_run failed")
        return {"error": str(exc), "content": None}
