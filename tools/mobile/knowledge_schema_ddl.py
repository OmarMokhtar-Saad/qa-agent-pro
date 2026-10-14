"""DDL of the knowledge db schema v2: the learned tables, natural keys and column adds."""

from __future__ import annotations

import sqlite3

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


def columns(conn: sqlite3.Connection, table: str) -> list:
    return [row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)]
