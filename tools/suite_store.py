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
from pathlib import Path

from config.settings import settings
from tools.install_paths import resolve_data_path
from tools.models import TestCase, TestSuite

logger = logging.getLogger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS suites (
    id           TEXT PRIMARY KEY,
    feature_text TEXT NOT NULL DEFAULT '',
    source_url   TEXT,
    created_at   REAL NOT NULL,
    created_by   TEXT,
    prep_id      TEXT
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


def _db_path() -> Path:
    # Install-root anchored -- see tools/install_paths. A cwd-relative store meant
    # qa_export_suite could not find a suite that plainly existed.
    return resolve_data_path(settings.qa_suite_store_path)


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Add what a NEWER build declares to an ALREADY-created suites table.

    F1c (2026-08-10): `suites.prep_id` plus a PARTIAL UNIQUE index on it is what
    makes a RETRIED host-mode finalize converge on the suite it already wrote
    instead of inserting a second row for the same generation.

    Both live HERE rather than in `_SCHEMA` for the same reason: `_SCHEMA` runs
    FIRST, and on an install whose suites.db predates the column a
    `CREATE ... INDEX ... ON suites(prep_id)` inside it would abort the whole
    `executescript` -- `CREATE TABLE IF NOT EXISTS` never alters an existing
    table, so the column would not exist yet. Running after the ALTER makes both
    safe on a fresh DB and on a legacy one.

    The index is PARTIAL (`WHERE prep_id IS NOT NULL`) so it constrains only
    prep-bearing rows: every pre-existing row and every save that passes no
    prep_id is NULL and therefore unconstrained -- SQLite treats NULLs as
    distinct anyway, but stating it makes the intent unmissable. It is the
    UNIQUENESS half of the fix: the upsert in `_save_suite_sync` needs a real
    constraint to conflict against, and without it two concurrent finalizes for
    one prep could still both insert.

    Idempotent, and never raises -- a failed migration degrades to the previous
    insert-only behaviour rather than breaking every suite save.
    """
    try:
        have = {r[1] for r in conn.execute("PRAGMA table_info(suites)").fetchall()}
        if "prep_id" not in have:
            conn.execute("ALTER TABLE suites ADD COLUMN prep_id TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_suites_prep_id "
            "ON suites(prep_id) WHERE prep_id IS NOT NULL"
        )
    except sqlite3.Error:
        logger.debug("suite_store: suites.prep_id migration skipped", exc_info=True)


def _drop_retired_tables(conn: sqlite3.Connection) -> None:
    """Shed schema a DELETED feature left behind in an ALREADY-created DB.

    F13 (2026-08-19): dead-code batch D3 (2026-08-15) deleted web suite
    execution outright -- ``tools/web_runner.py``, the ``qa_run_web_suite`` /
    ``qa_submit_web_run`` tools and this module's own ``web_runs`` DDL and
    save/load pair. What it could not delete is the table inside a suites.db
    that already existed: ``CREATE TABLE IF NOT EXISTS`` only ever ADDS, so an
    install whose store predates that date carries ``web_runs``, its
    ``idx_web_runs_suite_id`` index and every row it ever wrote through every
    future upgrade, advertising a capability the product no longer has.

    Dropping it here rather than in a one-off script is what makes the
    disposition hold: the row data and the declaration go in the SAME statement
    (SQLite drops a table's indexes with it), and no DDL remains anywhere in the
    tree that could recreate it, so the table cannot reappear empty on the next
    start.

    Discarded rows are DISCLOSED, not deleted silently -- a populated
    ``web_runs`` held per-run browser detail against a real ``base_url``, and an
    operator who wanted it back deserves to learn from the log that it went and
    how much. The COUNT is logged; the payload and the URL are not, because they
    are captured external content and a log line is not the place for them.

    Idempotent (the second connect finds no table and does nothing) and never
    raises -- a store that cannot drop a dead table must still save suites.
    """
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("web_runs",),
        ).fetchone()
        if row is None:
            return
        count = conn.execute("SELECT COUNT(*) FROM web_runs").fetchone()[0]
        if count:
            logger.warning(
                "suite_store: dropping retired table 'web_runs' and discarding "
                "%d row(s) -- web suite execution was deleted on 2026-08-15 "
                "(dead-code batch D3). Restore a backup of suites.db if the run "
                "history is still wanted.",
                count,
            )
        conn.execute("DROP TABLE web_runs")
    except sqlite3.Error:
        logger.debug("suite_store: web_runs drop skipped", exc_info=True)


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
    _ensure_columns(conn)
    # AFTER _ensure_columns on purpose: the prep_id migration is the one
    # that changes behaviour if it fails, so nothing this function does
    # can be what stopped it.
    _drop_retired_tables(conn)
    return conn


# --------------------------------------------------------------------------- #
# Sync workers (run inside asyncio.to_thread)
# --------------------------------------------------------------------------- #


def _save_suite_sync(
    suite: TestSuite,
    feature_text: str,
    source_url: str | None,
    created_by: str | None,
    prep_id: str | None = None,
) -> str:
    now = time.time()
    conn = _connect()
    try:
        with conn:  # transaction
            # ONE atomic statement, deliberately NOT a SELECT-then-INSERT-or-
            # UPDATE: each save_suite call runs on its OWN connection in its own
            # thread, so a read-then-branch is a TOCTOU race -- two finalizes for
            # the same prep (exactly what the retry-during-the-save-folder-dialog
            # scenario produces) could both observe "no row yet" and both insert,
            # which is the bug this is here to prevent. The partial UNIQUE index
            # created in _ensure_columns is what the conflict resolves against;
            # SQLite serialises the two writers and the loser takes the DO UPDATE
            # branch, keeping the ORIGINAL row's id (`excluded.id` is never
            # assigned) and its prep_id.
            #
            # `INSERT OR REPLACE` is retained for the id-conflict case so a save
            # that carries NO prep_id behaves byte-identically to before this fix.
            conn.execute(
                "INSERT OR REPLACE INTO suites "
                "(id, feature_text, source_url, created_at, created_by, prep_id) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(prep_id) WHERE prep_id IS NOT NULL DO UPDATE SET "
                "feature_text = excluded.feature_text, "
                "source_url = excluded.source_url, "
                "created_at = excluded.created_at, "
                "created_by = excluded.created_by",
                (
                    suite.suite_id,
                    feature_text or "",
                    source_url,
                    now,
                    created_by,
                    prep_id or None,
                ),
            )
            target_id = suite.suite_id
            if prep_id:
                # Which row survived. Safe to read: this connection already holds
                # the write lock taken by the statement above, so no other
                # finalize can interleave between the write and this lookup.
                row = conn.execute(
                    "SELECT id FROM suites WHERE prep_id = ? LIMIT 1", (prep_id,)
                ).fetchone()
                if row:
                    target_id = row[0]
            # Replace the suite's cases wholesale so a re-save is idempotent.
            conn.execute("DELETE FROM cases WHERE suite_id = ?", (target_id,))
            conn.executemany(
                "INSERT INTO cases (suite_id, stable_id, payload_json, version, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        target_id,
                        tc.stable_id,
                        tc.model_dump_json(),
                        1,
                        now,
                    )
                    for tc in suite.test_cases
                ],
            )
        return target_id
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


def _case_row_stats_sync(suite_id: str) -> tuple[bool, int]:
    """(does the suite ROW exist, how many case rows it has).

    F21 (2026-09-02 audit): _load_suite_sync returns None both for an
    unknown id and for a suite whose every case row failed validation, so
    its callers could only ever report the first. This answers the question
    that separates them, and it is asked ONLY on the None path, so a normal
    load costs nothing.
    """
    conn = _connect()
    try:
        row = conn.execute("SELECT id FROM suites WHERE id = ?", (suite_id,)).fetchone()
        if row is None:
            return False, 0
        counted = conn.execute(
            "SELECT COUNT(*) FROM cases WHERE suite_id = ?", (suite_id,)
        ).fetchone()
        return True, int((counted or (0,))[0] or 0)
    finally:
        conn.close()


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
    prep_id: str | None = None,
) -> dict:
    """Persist a suite and its cases. Returns {"content": {"suite_id": ...}}.

    *prep_id* (host mode) makes a RETRIED finalize idempotent: a second save for
    the same prep UPDATES the suite row that prep already produced and returns
    THAT row's id, instead of leaving two suites behind for one generation. The
    guarantee is a partial UNIQUE index plus a single upsert statement, so it
    holds even when the two finalizes run CONCURRENTLY. Omitted (the default)
    the behaviour is byte-identical to before.
    """
    try:
        suite_id = await asyncio.to_thread(
            _save_suite_sync, suite, feature_text, source_url, created_by, prep_id
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
        if suite is None:
            # F21: say WHICH kind of nothing this is. `exists` True with a
            # non-zero `row_total` means the suite IS on disk and every one
            # of its rows failed to load -- reporting that as 'no stored
            # suite, generate one first' told the tester their cases were
            # gone when they were not.
            exists, row_total = await asyncio.to_thread(_case_row_stats_sync, suite_id)
            return {
                "error": None,
                "content": None,
                "skipped": row_total if exists else 0,
                "exists": bool(exists),
                "row_total": int(row_total),
            }
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
    It was added for tools/zephyr_exporter.py, whose Project / Issue columns
    came from the Jira story key. That module and its caller
    ``mcp_handlers._suite_story_key`` were DELETED on 2026-08-15 (dead-code
    deletion batch D4), so this accessor has NO production caller today. It
    is RETAINED on purpose -- a general-purpose store read on a live module,
    not a zephyr helper -- and tests/test_suite_store.py is its cover. No
    schema change: every column already existed. Never raises.
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
            # suite's feature_text) — the M1-web fix, first written for the
            # web-run persistence block batch D3 deleted on 2026-08-15.
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
