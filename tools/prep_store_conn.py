"""Prep store connection, schema, migration and purge (leaf of ``tools.prep_store``)."""

from __future__ import annotations

import logging
import sqlite3
import time

from tools import sqlite_store
from tools.prep_store_cfg import _BUSY_TIMEOUT_S, _db_path, _max_lifetime_s

logger = logging.getLogger("tools.prep_store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS preps (
    id           TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    created_at   REAL NOT NULL,
    created_by   TEXT,
    touched_at   REAL
);
CREATE TABLE IF NOT EXISTS prep_submissions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    prep_id       TEXT NOT NULL,
    category_name TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    created_at    REAL NOT NULL,
    UNIQUE(prep_id, category_name),
    FOREIGN KEY (prep_id) REFERENCES preps(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_prep_submissions_prep_id
    ON prep_submissions(prep_id);
"""


def _has_touched_at(conn: sqlite3.Connection) -> bool:
    """Whether preps.touched_at exists on THIS database. Probe, never raises."""
    try:
        rows = conn.execute("PRAGMA table_info(preps)").fetchall()
    except sqlite3.Error:
        return False
    return any(r[1] == "touched_at" for r in rows)


def alter_add_touched_at(conn: sqlite3.Connection) -> None:
    """The single ALTER, isolated so a test can simulate a locked database."""
    conn.execute("ALTER TABLE preps ADD COLUMN touched_at REAL")


# PROBE FIRST, and report back whether the column is usable. A bare
# ``except sqlite3.OperationalError: pass`` around the ALTER would also
# swallow "database is locked" -- which this module's docstring calls the
# NORMAL concurrent multi-client case -- and every later
# ``SELECT ... touched_at`` would then raise "no such column", which
# load_prep converts to a miss: i.e. every LIVE prep would read as unknown
# or expired, the exact loss this change exists to prevent. Never raises.
def _ensure_touched_at(conn: sqlite3.Connection) -> bool:
    """Add preps.touched_at to a DB created before the sliding TTL existed."""
    if _has_touched_at(conn):
        return True
    try:
        with conn:  # transaction
            alter_add_touched_at(conn)
    except sqlite3.Error:
        # Locked/read-only DB, or a racing process won -- re-probe, never guess.
        logger.debug("prep_store: touched_at migration deferred", exc_info=True)
        return _has_touched_at(conn)
    return True


def _open_conn() -> sqlite3.Connection:
    return sqlite_store.connect(
        _db_path(),
        sqlite_store.ConnectOpts(
            timeout=_BUSY_TIMEOUT_S,
            wal=True,
            busy_timeout_ms=int(_BUSY_TIMEOUT_S * 1000),
            pragmas_best_effort=True,
            foreign_keys=True,
            logger=logger,
            log_label="prep_store",
        ),
    )


# PRAGMA foreign_keys = ON so the declared ON DELETE CASCADE on
# prep_submissions actually fires (SQLite defaults it OFF per connection).
#
# journal_mode = WAL + a busy_timeout because host mode makes CONCURRENT
# multi-client use the normal case, not the exception: each MCP host (Claude
# Desktop, Cursor, ChatGPT, ...) runs its own server process against this same
# SQLite file, so two testers preparing/submitting at the same moment would
# otherwise race on the default rollback journal and surface "database is
# locked". WAL lets readers and one writer proceed concurrently; the timeout
# makes a genuinely contended write wait instead of failing instantly. Both
# pragmas are best-effort — an older SQLite or a filesystem that cannot do WAL
# must not break the store, so a failure here is logged and ignored (the
# never-raise contract still holds via the callers' error dicts).
def _connect() -> sqlite3.Connection:
    """Open the DB (creating parent dirs + schema). Caller must close."""
    conn = _open_conn()
    try:
        conn.executescript(_SCHEMA)
        # Best-effort migration for DBs created before touched_at existed.
        # Probe-first and non-fatal: readers re-probe and degrade to the
        # fixed TTL rather than ever selecting a missing column.
        _ensure_touched_at(conn)
    except BaseException:
        conn.close()
        raise
    return conn


# F8 (2026-08-15). Until now the only expiry path was _load_prep_sync, which
# evaluates _expired() for ONE prep_id -- the one a caller already named. A
# prep nobody ever loads again was therefore never examined and never
# deleted: data/suites.db held 205 rows reaching back to 2026-07-28 under
# QA_PREP_TTL_S=3600 and QA_PREP_MAX_LIFETIME_S=14400. The TTL governed
# USABILITY, never RETENTION, and each of those payloads carries the ticket's
# full description, its comment thread and its sibling stories, so the gap is
# a data-retention one rather than a disk-space one.
#
# WHY THE HARD CAP AND NOT THE SLIDING TTL: _expired() restarts the TTL clock
# at the last touch, so a prep inside its TTL may still be live for a flow
# this process knows nothing about (host mode means several MCP server
# processes share this one file). ``created_at + _max_lifetime_s()`` is the
# one bound no touch can extend -- _expired() returns True for every row past
# it unconditionally -- so a row this deletes could not have been loaded
# successfully by anyone anyway. Deleting it destroys no reachable work.
#
# prep_submissions rows go with it via ON DELETE CASCADE (_connect sets
# PRAGMA foreign_keys = ON). Caller supplies the connection and owns the
# transaction. Never raises -- retention hygiene must not break a save.
def _purge_expired_sync(conn: sqlite3.Connection) -> int:
    """Delete every prep past the HARD lifetime cap. Returns the row count."""
    try:
        cutoff = time.time() - _max_lifetime_s()
        cur = conn.execute("DELETE FROM preps WHERE created_at < ?", (cutoff,))
        return int(cur.rowcount or 0)
    except sqlite3.Error:
        logger.debug("prep_store: expired-prep purge skipped", exc_info=True)
        return 0


# Envelope key stamped on a prep whose suite FINALIZED successfully. 2026-08-03
# (Fix 5b): finalize used to DELETE the prep, so "prep gone" meant three different
# things -- unknown id, expired TTL, and finished work -- and every reader keyed off
# that one signal. A host that resubmitted after a successful finalize was told to
# "start again with qa_prepare_test_cases", i.e. to regenerate a suite that already
# existed. Keeping the record and stamping it lets each reader answer precisely.
#
# RETENTION NOTE: the payload holds the ticket source text, so keeping it until the
# TTL lengthens how long that content lives here versus deleting at finalize. The
# stamp itself carries only a suite_id and an export path, and the TTL still reaps
# the record -- _expired reads created_at/touched_at, and update_prep leaves
# created_at untouched, so a stamped prep can never become immortal.
FINALIZED_KEY = "finalized"


def is_finalized_record(record: object) -> bool:
    """Whether a loaded prep envelope belongs to an already-finalized suite.

    Mirrors tools.host_llm.is_host_task_record: a cheap, never-raising shape test
    the handlers can run at every load site before rehydrating anything.
    """
    return isinstance(record, dict) and isinstance(record.get(FINALIZED_KEY), dict)


def _alter_add_touched_at(conn: sqlite3.Connection) -> None:
    """Legacy name of :func:`alter_add_touched_at` (WX removes it)."""
    return alter_add_touched_at(conn)
