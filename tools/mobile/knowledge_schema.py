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

import json
import logging
import sqlite3
import time

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "2"

#: Common columns of every learned table (after ``id``).
_COMMON = """
    app_version TEXT NOT NULL DEFAULT '',
    source_run TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL,
    valid_from REAL NOT NULL,
    invalid_at REAL,
    trust TEXT NOT NULL DEFAULT 'learned',
    status TEXT NOT NULL DEFAULT 'candidate',
    confirmed INTEGER DEFAULT 0,
    contradicted INTEGER DEFAULT 0,
    supersedes INTEGER,
    runs_json TEXT DEFAULT '[]'
"""

#: table -> its own columns (the common ones are appended).
_TABLES = {
    "lessons": (
        "text TEXT NOT NULL DEFAULT '', screen_key TEXT NOT NULL DEFAULT '', "
        "votes INTEGER DEFAULT 0, retention REAL DEFAULT 1.0, last_used_run TEXT DEFAULT ''"
    ),
    "shortcuts": (
        "name TEXT NOT NULL, args_json TEXT DEFAULT '{}', precondition_json TEXT DEFAULT '{}', "
        "steps_json TEXT DEFAULT '[]', origin TEXT DEFAULT '', success INTEGER DEFAULT 0, "
        "fail INTEGER DEFAULT 0, start_key TEXT DEFAULT '', end_key TEXT DEFAULT ''"
    ),
    "elements": (
        "screen_key TEXT NOT NULL DEFAULT '', fp_json TEXT DEFAULT '{}', fp_hash TEXT NOT NULL, "
        "locators_json TEXT DEFAULT '[]', bound_field TEXT DEFAULT '', recheck_at TEXT DEFAULT '', "
        "missing_at TEXT DEFAULT '', hits INTEGER DEFAULT 0, misses INTEGER DEFAULT 0"
    ),
    "screens": (
        "screen_key TEXT NOT NULL, activity TEXT DEFAULT '', anchors_json TEXT DEFAULT '[]', "
        "title TEXT DEFAULT '', remap_of TEXT DEFAULT ''"
    ),
    "edges": (
        "from_key TEXT NOT NULL, action TEXT NOT NULL DEFAULT '', target TEXT NOT NULL DEFAULT '', "
        "to_key TEXT NOT NULL DEFAULT '', count INTEGER DEFAULT 0, p50_ms INTEGER DEFAULT 0, "
        "p90_ms INTEGER DEFAULT 0"
    ),
    "timings": (
        "screen_key TEXT NOT NULL DEFAULT '', action TEXT NOT NULL DEFAULT '', "
        "target TEXT NOT NULL DEFAULT '', samples_json TEXT DEFAULT '[]', n INTEGER DEFAULT 0, "
        "p90_ms INTEGER DEFAULT 0, wait_ms INTEGER DEFAULT 0"
    ),
    "popups": (
        "signature TEXT NOT NULL, dismiss_json TEXT DEFAULT '{}', times_seen INTEGER DEFAULT 0, "
        "runs_seen INTEGER DEFAULT 0"
    ),
    "mistakes": (
        "screen_key TEXT NOT NULL DEFAULT '', element_fp TEXT NOT NULL DEFAULT '', "
        "bad_pattern TEXT NOT NULL DEFAULT '', symptom TEXT DEFAULT '', fix TEXT DEFAULT '', "
        "count INTEGER DEFAULT 0, runs_seen INTEGER DEFAULT 0"
    ),
}

#: table -> natural key columns (one OPEN row per key).
NATURAL_KEYS = {
    "timings": ("screen_key", "action", "target"),
    "edges": ("from_key", "action", "target", "to_key"),
    "popups": ("signature",),
    "screens": ("screen_key",),
    "mistakes": ("screen_key", "element_fp", "bad_pattern"),
    "shortcuts": ("name",),
    "elements": ("fp_hash",),
}

#: Tables that carry the common columns and are written through knowledge_db.
LEARNED_TABLES = ("notes",) + tuple(_TABLES)

_RUNS = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    app_version TEXT NOT NULL DEFAULT '',
    version_code TEXT NOT NULL DEFAULT '',
    env_json TEXT NOT NULL DEFAULT '{}',
    started REAL,
    finished REAL,
    verdict TEXT NOT NULL DEFAULT '',
    learn_state TEXT NOT NULL DEFAULT 'unlearned',
    learned_at REAL,
    learn_error TEXT NOT NULL DEFAULT '',
    learn_tries INTEGER NOT NULL DEFAULT 0,
    reflect_state TEXT NOT NULL DEFAULT 'none',
    summary_json TEXT NOT NULL DEFAULT '{}'
);
"""

_NOTES_ADD = (
    ("screen_key", "TEXT DEFAULT ''"),
    ("element_fp", "TEXT DEFAULT ''"),
    ("recheck_at", "TEXT DEFAULT ''"),
    ("runs_json", "TEXT DEFAULT '[]'"),
    ("created", "REAL DEFAULT 0"),
    ("variant_of", "INTEGER"),
    ("proposal_json", "TEXT DEFAULT ''"),
)
_EVENTS_ADD = (
    ("table_name", "TEXT DEFAULT ''"),
    ("row_id", "INTEGER"),
    ("before_json", "TEXT DEFAULT ''"),
)


def _columns(conn: sqlite3.Connection, table: str) -> list:
    return [row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)]


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
            "CREATE INDEX IF NOT EXISTS ix_%s_screen ON %s (screen_key, status)" % (table, table)
        )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_edges_from ON edges (from_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_events_run ON events (run_id)")


def migrate(conn: sqlite3.Connection) -> bool:
    """Bring *conn* to schema v2. Idempotent; never raises (False on failure)."""
    try:
        _add_columns(conn, "notes", _NOTES_ADD)
        _add_columns(conn, "events", _EVENTS_ADD)
        _create_tables(conn)
        _create_indexes(conn)
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (SCHEMA_VERSION,),
        )
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


def _load(raw: object) -> dict:
    try:
        value = json.loads(str(raw or "{}"))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _restore(conn: sqlite3.Connection, table: str, row_id: int, before: dict) -> bool:
    cols = set(_columns(conn, table))
    items = [(k, v) for k, v in before.items() if k in cols and k != "id"]
    if not items:
        return False
    sets = ", ".join("%s = ?" % k for k, _ in items)
    conn.execute(
        "UPDATE %s SET %s WHERE id = ?" % (table, sets), [v for _, v in items] + [row_id]
    )
    return True


def _undo_one(conn: sqlite3.Connection, kind: str, table: str, row_id: int, raw: str) -> bool:
    """Undo one event. ``kind`` is the event name written by knowledge_db."""
    before = _load(raw)
    if kind == "insert":
        conn.execute(
            "UPDATE %s SET status = 'retired', invalid_at = COALESCE(invalid_at, ?) WHERE id = ?"
            % table,
            (time.time(), row_id),
        )
        return True
    if kind == "supersede":
        conn.execute(
            "UPDATE %s SET status = 'retired', invalid_at = COALESCE(invalid_at, ?) WHERE id = ?"
            % table,
            (time.time(), row_id),
        )
        old = before.get("old") if isinstance(before.get("old"), dict) else {}
        old_id = old.get("id")
        if isinstance(old_id, int):
            _restore(conn, table, old_id, old)
        return True
    if kind in ("update", "counter", "status"):
        return _restore(conn, table, row_id, before)
    return False


def rollback_run(conn: sqlite3.Connection, run_id: str) -> dict:
    """Undo everything *run_id* wrote, newest first. Never raises.

    Returns ``{"undone": n}`` (plus ``"error"`` on failure). A second call is a
    no-op: events older than the run's last ``rollback`` event are skipped."""
    try:
        run_id = str(run_id or "")
        if not run_id:
            return {"undone": 0, "error": "no run id"}
        last = conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM events WHERE run_id = ? AND event = 'rollback'",
            (run_id,),
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT event, table_name, row_id, before_json FROM events "
            "WHERE run_id = ? AND id > ? AND table_name != '' AND row_id IS NOT NULL "
            "ORDER BY id DESC",
            (run_id, last),
        ).fetchall()
        undone = 0
        for row in rows:
            table = str(row[1])
            if table not in LEARNED_TABLES:
                continue
            if _undo_one(conn, str(row[0]), table, int(row[2]), str(row[3] or "")):
                undone += 1
        conn.execute(
            "INSERT INTO events (ts, note_id, event, detail, run_id, table_name, row_id, before_json) "
            "VALUES (?, NULL, 'rollback', ?, ?, '', NULL, '')",
            (time.time(), "undone %d" % undone, run_id[:64]),
        )
        conn.commit()
        return {"undone": undone}
    except (sqlite3.Error, ValueError, TypeError, KeyError, OSError) as exc:
        logger.warning("knowledge schema: rollback failed", exc_info=True)
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        return {"undone": 0, "error": "rollback failed: %s" % type(exc).__name__}
