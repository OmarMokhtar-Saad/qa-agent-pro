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
CREATE TABLE IF NOT EXISTS checklists (
    suite_id     TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    created_at   REAL NOT NULL,
    FOREIGN KEY (suite_id) REFERENCES suites(id) ON DELETE CASCADE
);
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
    cases = []
    skipped = 0
    first_err = None
    known = set(TestCase.model_fields)
    for r in case_rows:
        try:
            cases.append(TestCase.model_validate_json(r[0]))
            continue
        except Exception as exc:
            # Keep the REAL reason: the retry below raises a synthetic error.
            first_err = first_err or exc
        # Forward compatibility: a row written by a NEWER build can carry fields
        # this one does not know, and TestCase sets extra="forbid". Drop only the
        # unknown keys and retry -- never guess at a value, never mutate a known
        # one. Additive drift only; a narrowed constraint still fails below.
        try:
            raw = json.loads(r[0])
            if not isinstance(raw, dict):
                raise ValueError("case payload is not an object")
            dropped = sorted(k for k in raw if k not in known)
            if not dropped:
                raise ValueError("no unknown keys to drop")
            cases.append(
                TestCase.model_validate({k: v for k, v in raw.items() if k in known})
            )
            logger.warning(
                "suite_store: case in suite %s carried unknown field(s) %s -- "
                "dropped them to load it (payload written by a newer build?)",
                suite_id,
                ", ".join(dropped),
            )
        except Exception:
            skipped += 1
            # At most a few lines: a wholly corrupt 64-case suite must not
            # emit 64 tracebacks, and the SYNTHETIC retry error is not the
            # interesting one -- first_err is.
            if skipped <= 3:
                logger.warning(
                    "suite_store: case in suite %s could not be loaded: %r",
                    suite_id,
                    first_err,
                )
    if skipped:
        logger.warning(
            "suite_store: %d case(s) in suite %s could not be loaded and are "
            "NOT in the returned suite",
            skipped,
            suite_id,
        )
    if not cases:
        return None
    suite = TestSuite(suite_id=suite_id, test_cases=cases)
    # Never swallow a shortened suite: mirrors _dropped_note's rule that a
    # dropped count must reach the tester, not just the log. Private attr, the
    # same channel _checklist_artifacts already uses.
    if skipped:
        suite._load_skipped = skipped
    return suite


def _load_suite_meta_sync(suite_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, feature_text, source_url, created_at, created_by "
            "FROM suites WHERE id = ?",
            (suite_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "suite_id": row[0],
        "feature_text": row[1],
        "source_url": row[2],
        "created_at": row[3],
        "created_by": row[4],
    }


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
        # Carry the shortfall OUT of the loader so a caller can disclose it;
        # silently exporting fewer cases than were stored is the failure mode.
        return {
            "error": None,
            "content": suite,
            "skipped": int(getattr(suite, "_load_skipped", 0) or 0),
        }
    except Exception as exc:
        logger.exception("suite_store.load_suite failed")
        return {"error": str(exc), "content": None}


async def load_suite_meta(suite_id: str) -> dict:
    """Load a suite's stored ROW metadata by id. Returns {"content": dict|None}.

    ``load_suite`` rebuilds the TestSuite from the persisted cases; this returns
    the suite ROW instead (feature_text / source_url / created_at / created_by).
    Exporters that need the originating ticket -- e.g. tools/zephyr_exporter.py,
    whose Project / Issue columns come from the Jira story key -- read it here.
    No schema change: every column already existed. Never raises.
    """
    try:
        if not suite_id:
            return {"error": None, "content": None}
        row = await asyncio.to_thread(_load_suite_meta_sync, suite_id)
        return {"error": None, "content": row}
    except Exception as exc:
        logger.exception("suite_store.load_suite_meta failed")
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


# --------------------------------------------------------------------------- #
# Atomic Requirements Checklist persistence (Batch 2)
#
# The checklist + its coverage audit are a DURABLE artifact, not an in-memory
# intermediate: a coverage claim must stay re-auditable after the session ends.
# --------------------------------------------------------------------------- #


def _save_checklist_sync(suite_id: str, payload: dict) -> str:
    now = time.time()
    conn = _connect()
    try:
        with conn:  # transaction
            # checklists.suite_id is an FK into suites and PRAGMA foreign_keys is
            # ON, so a checklist for an in-session suite that was never persisted
            # would raise IntegrityError and be silently dropped. Upsert a stub
            # suites row first (INSERT OR IGNORE never touches an existing
            # suite's feature_text) — same fix as _save_web_run_sync (M1-web).
            conn.execute(
                "INSERT OR IGNORE INTO suites (id, feature_text, created_at) "
                "VALUES (?, '', ?)",
                (suite_id, now),
            )
            conn.execute(
                "INSERT OR REPLACE INTO checklists "
                "(suite_id, payload_json, created_at) VALUES (?, ?, ?)",
                (suite_id, json.dumps(payload), now),
            )
        return suite_id
    finally:
        conn.close()


def _load_checklist_sync(suite_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT payload_json FROM checklists WHERE suite_id = ?", (suite_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except (ValueError, TypeError):
        return None


async def save_checklist(suite_id: str, payload: dict) -> dict:
    """Persist a suite's atomic checklist + coverage audit. Never raises."""
    try:
        if not suite_id:
            return {"error": "suite_id is required", "content": None}
        await asyncio.to_thread(_save_checklist_sync, suite_id, payload or {})
        logger.info(
            "suite_store: saved checklist for suite %s (%d item(s))",
            suite_id,
            len((payload or {}).get("items") or []),
        )
        return {"error": None, "content": {"suite_id": suite_id}}
    except Exception as exc:
        logger.exception("suite_store.save_checklist failed")
        return {"error": str(exc), "content": None}


async def load_checklist(suite_id: str) -> dict:
    """Load a suite's persisted checklist payload. Returns
    ``{"content": dict|None}`` (None when unknown). Never raises."""
    try:
        if not suite_id:
            return {"error": None, "content": None}
        payload = await asyncio.to_thread(_load_checklist_sync, suite_id)
        return {"error": None, "content": payload}
    except Exception as exc:
        logger.exception("suite_store.load_checklist failed")
        return {"error": str(exc), "content": None}
