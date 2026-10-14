"""SQLite plumbing for ``tools.suite_store``: connect, schema upgrades, retired tables.

Split out of the facade so that module stays small. The facade owns ``_SCHEMA`` and
``_db_path`` and passes them in, so tests that rebind them on ``suite_store`` keep
biting.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from tools import sqlite_store

logger = logging.getLogger("tools.suite_store")


# Add what a NEWER build declares to an ALREADY-created suites table.
#
# F1c (2026-08-10): `suites.prep_id` plus a PARTIAL UNIQUE index on it is what
# makes a RETRIED host-mode finalize converge on the suite it already wrote
# instead of inserting a second row for the same generation.
#
# Both live HERE rather than in `_SCHEMA` for the same reason: `_SCHEMA` runs
# FIRST, and on an install whose suites.db predates the column a
# `CREATE ... INDEX ... ON suites(prep_id)` inside it would abort the whole
# `executescript` -- `CREATE TABLE IF NOT EXISTS` never alters an existing
# table, so the column would not exist yet. Running after the ALTER makes both
# safe on a fresh DB and on a legacy one.
#
# The index is PARTIAL (`WHERE prep_id IS NOT NULL`) so it constrains only
# prep-bearing rows: every pre-existing row and every save that passes no
# prep_id is NULL and therefore unconstrained -- SQLite treats NULLs as
# distinct anyway, but stating it makes the intent unmissable. It is the
# UNIQUENESS half of the fix: the upsert in `_save_suite_sync` needs a real
# constraint to conflict against, and without it two concurrent finalizes for
# one prep could still both insert.
#
# Idempotent, and never raises -- a failed migration degrades to the previous
# insert-only behaviour rather than breaking every suite save.
def ensure_columns(conn: sqlite3.Connection) -> None:
    """Add what a NEWER build declares to an ALREADY-created suites table."""
    _ensure_prep_id(conn)
    _ensure_provenance(conn)


def _ensure_prep_id(conn: sqlite3.Connection) -> None:
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


# v1.97.0 cursor-hardening (item 3b): suites.provenance_json, in its own
# try/except so a failure here cannot block the prep_id migration (or vice
# versa) -- same idempotent, pre-existing-DB pattern.
def _ensure_provenance(conn: sqlite3.Connection) -> None:
    try:
        have2 = {r[1] for r in conn.execute("PRAGMA table_info(suites)").fetchall()}
        if "provenance_json" not in have2:
            conn.execute("ALTER TABLE suites ADD COLUMN provenance_json TEXT")
    except sqlite3.Error:
        logger.debug(
            "suite_store: suites.provenance_json migration skipped", exc_info=True
        )


# Shed schema a DELETED feature left behind in an ALREADY-created DB.
#
# F13 (2026-08-19): dead-code batch D3 (2026-08-15) deleted web suite
# execution outright -- ``tools/web_runner.py``, the ``qa_run_web_suite`` /
# ``qa_submit_web_run`` tools and this module's own ``web_runs`` DDL and
# save/load pair. What it could not delete is the table inside a suites.db
# that already existed: ``CREATE TABLE IF NOT EXISTS`` only ever ADDS, so an
# install whose store predates that date carries ``web_runs``, its
# ``idx_web_runs_suite_id`` index and every row it ever wrote through every
# future upgrade, advertising a capability the product no longer has.
#
# Dropping it here rather than in a one-off script is what makes the
# disposition hold: the row data and the declaration go in the SAME statement
# (SQLite drops a table's indexes with it), and no DDL remains anywhere in the
# tree that could recreate it, so the table cannot reappear empty on the next
# start.
#
# Discarded rows are DISCLOSED, not deleted silently -- a populated
# ``web_runs`` held per-run browser detail against a real ``base_url``, and an
# operator who wanted it back deserves to learn from the log that it went and
# how much. The COUNT is logged; the payload and the URL are not, because they
# are captured external content and a log line is not the place for them.
#
# Idempotent (the second connect finds no table and does nothing) and never
# raises -- a store that cannot drop a dead table must still save suites.
def drop_retired_tables(conn: sqlite3.Connection) -> None:
    """Shed schema a DELETED feature left behind in an ALREADY-created DB."""
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("web_runs",),
        ).fetchone()
        if row is None:
            return
        _drop_web_runs(conn)
    except sqlite3.Error:
        logger.debug("suite_store: web_runs drop skipped", exc_info=True)


def _drop_web_runs(conn: sqlite3.Connection) -> None:
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


# foreign_keys=True enforces declared FOREIGN KEY ... ON DELETE CASCADE
# (NB-019). SQLite defaults this OFF per-connection, so the schema's cascade
# is inert without it — a future suite-delete path would otherwise leave
# orphaned `cases` rows.
def _connect_raw(path: Path) -> sqlite3.Connection:
    return sqlite_store.connect(
        path,
        sqlite_store.ConnectOpts(
            timeout=5.0,
            wal=True,
            busy_timeout_ms=5000,
            pragmas_best_effort=True,
            foreign_keys=True,
            logger=logger,
            log_label="suite_store",
        ),
    )


def open_db(path: Path, schema: str) -> sqlite3.Connection:
    """Open the DB (creating parent dirs + schema). Caller must close."""
    conn = _connect_raw(path)
    try:
        conn.executescript(schema)
        ensure_columns(conn)
        # AFTER ensure_columns on purpose: the prep_id migration is the one
        # that changes behaviour if it fails, so nothing this function does
        # can be what stopped it.
        drop_retired_tables(conn)
    except BaseException:
        conn.close()
        raise
    return conn
