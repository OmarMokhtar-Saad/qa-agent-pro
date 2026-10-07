"""The ONLY write path into the per-app knowledge database (schema v2).

Every helper: never raises; refuses a secret-looking string (``refused`` event,
``None`` back); checks the per-table row cap (compacting once before refusing);
emits exactly one ``events`` row carrying ``before_json`` so a run can be rolled
back (``knowledge_schema.rollback_run``). Rows are never deleted by learning:
``supersede`` closes the old row and inserts the new one.

Transactions: when the caller already opened one (``conn.in_transaction``) the
helper leaves commit/rollback to it; otherwise it commits its own work.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time

from tools.mobile import app_knowledge, knowledge_limits, knowledge_schema

logger = logging.getLogger(__name__)

STATUSES = (
    "candidate",
    "active",
    "stale",
    "disputed",
    "needs_recheck",
    "superseded",
    "retired",
)
_CLOSED = ("superseded", "retired")
#: Columns holding ids, keys and hashes, not prose: never scanned for secrets.
_STRUCTURAL = frozenset(
    {
        "source_run",
        "app_version",
        "trust",
        "status",
        "runs_json",
        "screen_key",
        "from_key",
        "to_key",
        "start_key",
        "end_key",
        "fp_hash",
        "element_fp",
        "fp_json",
        "anchors_json",
        "samples_json",
        "last_used_run",
        "created",
        "valid_from",
        "invalid_at",
        "recheck_at",
        "missing_at",
        "run_id",
        "version_code",
        "learn_state",
        "reflect_state",
    }
)
_DETAIL_CHARS = 200
_NO_WRITE = frozenset({"id"})


#: Failures a helper turns into a safe default; anything else is a bug.
_ERRORS = (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError, OSError)


def open_rw(package: object):
    """A write connection to the app's knowledge db, or ``None``. Never raises."""
    try:
        path = app_knowledge._store_path(package)
        if path is None:
            return None
        return app_knowledge._connect(path, write=True)
    except _ERRORS:
        logger.info("knowledge db: could not open for write", exc_info=True)
        return None


def parse_item_id(item_id: object):
    """``"notes:3"`` -> ``("notes", 3)``; ``None`` for anything else."""
    try:
        table, _, num = str(item_id or "").partition(":")
        if table not in knowledge_schema.LEARNED_TABLES or not num.isdigit():
            return None
        return table, int(num)
    except _ERRORS:
        return None


def _dump(value: object) -> object:
    return json.dumps(value, sort_keys=True) if isinstance(value, (dict, list, tuple)) else value


def _event(conn, name: str, where: tuple, what: tuple) -> None:
    table, row_id = where
    run_id, why, before = what
    conn.execute(
        "INSERT INTO events (ts, note_id, event, detail, run_id, table_name, row_id, before_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            time.time(),
            row_id if table == "notes" else None,
            name,
            str(why or "")[:_DETAIL_CHARS],
            str(run_id or "")[:64],
            table,
            row_id,
            json.dumps(before, sort_keys=True) if before else "",
        ),
    )


def _secret(values: dict, run_id: str) -> str:
    """The refusal reason for the first prose string in *values*, or ``""``."""
    for col, value in values.items():
        if col in _STRUCTURAL:
            continue
        for leaf in app_knowledge._leaf_strings(value):
            reason = app_knowledge.secret_reason(leaf, (), run_id)
            if reason:
                return "%s: %s" % (col, reason)
    return ""


def _cap(table: str) -> int:
    if table == "notes":
        return app_knowledge.MAX_NOTE_ROWS
    return knowledge_limits.MAX_ROWS.get(table, 0)


def _count(conn, table: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]


def _full(conn, table: str) -> bool:
    cap = _cap(table)
    if not cap or _count(conn, table) < cap:
        return False
    compact(conn, table)
    return _count(conn, table) >= cap


def _finish(conn, owns: bool, ok: bool) -> None:
    if not owns:
        return
    if ok:
        conn.commit()
    else:
        conn.rollback()


def _refuse(conn, table: str, run_id: str, why: object, reason: str) -> None:
    try:
        _event(conn, "refused", (table, None), (run_id, "%s (%s)" % (reason, why), None))
    except sqlite3.Error:
        logger.info("knowledge db: could not log a refusal")


def _row(table: str, cols: list, values: dict, run_id: str) -> dict:
    now = time.time()
    defaults = {
        "created": now,
        "valid_from": now,
        "source_run": run_id,
        "trust": "learned",
        "status": "candidate",
        "runs_json": json.dumps([run_id] if run_id else []),
    }
    row = {k: v for k, v in defaults.items() if k in cols}
    row.update({k: _dump(v) for k, v in values.items() if k in cols and k not in _NO_WRITE})
    return row


def _insert_row(conn, table: str, values: dict, run_id: str) -> tuple:
    """``(row_id, "")`` or ``(None, reason)``. Writes no event."""
    if table not in knowledge_schema.LEARNED_TABLES and table != "runs":
        return None, "unknown table"
    cols = knowledge_schema.table_columns(conn, table)
    if not cols:
        return None, "table missing"
    row = _row(table, cols, values, run_id)
    if not row:
        return None, "no known columns"
    reason = _secret(row, run_id)
    if reason:
        return None, "secret " + reason
    if _full(conn, table):
        return None, "cap: %s is full" % table
    names = list(row)
    cur = conn.execute(
        "INSERT INTO %s (%s) VALUES (%s)" % (table, ", ".join(names), ", ".join("?" * len(names))),
        [row[n] for n in names],
    )
    return cur.lastrowid, ""


def insert(conn, table: str, values: dict, *, run_id: str = "", why: str = ""):
    """Insert one row (defaults, secret refusal, cap, event). Row id or ``None``."""
    owns = _owns(conn)
    try:
        row_id, reason = _insert_row(conn, table, values or {}, str(run_id or ""))
        if row_id is None:
            _refuse(conn, table, run_id, why, reason)
            _finish(conn, owns, True)
            return None
        _event(conn, "insert", (table, row_id), (run_id, why, None))
        _finish(conn, owns, True)
        return row_id
    except sqlite3.IntegrityError:
        _finish(conn, owns, False)
        return None
    except _ERRORS:
        logger.info("knowledge db: insert failed", exc_info=True)
        _safe_rollback(conn, owns)
        return None


def _owns(conn) -> bool:
    try:
        return not conn.in_transaction
    except sqlite3.Error:
        return True


def _safe_rollback(conn, owns: bool) -> None:
    if owns:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass


def _open_row(conn, table: str, key: dict):
    cols = set(knowledge_schema.table_columns(conn, table))
    if not key or not set(key) <= cols:
        return None
    where = " AND ".join("%s = ?" % k for k in key)
    return conn.execute(
        "SELECT * FROM %s WHERE %s AND invalid_at IS NULL ORDER BY id DESC LIMIT 1" % (table, where),
        [_dump(v) for v in key.values()],
    ).fetchone()


def _bump_runs(raw: object, run_id: str) -> str:
    try:
        runs = [r for r in json.loads(str(raw or "[]")) if isinstance(r, str)]
    except ValueError:
        runs = []
    if run_id and run_id not in runs:
        runs.append(run_id)
    return json.dumps(runs[-knowledge_limits.MAX_RUNS_PER_ITEM :])


def upsert_counter(conn, table: str, key: dict, deltas: dict, **ctx):
    """Add *deltas* to the open row at natural *key*, or insert it. Row id or ``None``."""
    owns = _owns(conn)
    try:
        run_id = str(ctx.pop("run_id", "") or "")
        why = ctx.pop("why", "")
        cols = set(knowledge_schema.table_columns(conn, table))
        deltas = {k: v for k, v in (deltas or {}).items() if k in cols and k not in _NO_WRITE}
        if table not in knowledge_schema.LEARNED_TABLES or not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in deltas.values()
        ):
            _finish(conn, owns, True)
            return None
        old = _open_row(conn, table, key)
        if old is None:
            return insert(conn, table, {**key, **deltas}, run_id=run_id, why=why)
        before = {c: old[c] for c in list(deltas) + ["runs_json"] if c in cols}
        sets = ["%s = COALESCE(%s, 0) + ?" % (c, c) for c in deltas]
        args: list = list(deltas.values())
        if "runs_json" in cols:
            sets.append("runs_json = ?")
            args.append(_bump_runs(old["runs_json"], run_id))
        if sets:
            conn.execute("UPDATE %s SET %s WHERE id = ?" % (table, ", ".join(sets)), args + [old["id"]])
        _event(conn, "counter", (table, old["id"]), (run_id, why, before))
        _finish(conn, owns, True)
        return old["id"]
    except _ERRORS:
        logger.info("knowledge db: upsert_counter failed", exc_info=True)
        _safe_rollback(conn, owns)
        return None


def supersede(conn, table: str, old_id: int, new_values: dict, **ctx):
    """Close row *old_id* and insert its replacement. New row id or ``None``."""
    owns = _owns(conn)
    try:
        run_id = str(ctx.pop("run_id", "") or "")
        why = ctx.pop("why", "")
        if table not in knowledge_schema.LEARNED_TABLES:
            return None
        old = conn.execute(
            "SELECT * FROM %s WHERE id = ? AND invalid_at IS NULL" % table, (old_id,)
        ).fetchone()
        if old is None:
            _finish(conn, owns, True)
            return None
        conn.execute("SAVEPOINT ak_supersede")
        now = time.time()
        conn.execute(
            "UPDATE %s SET status = 'superseded', invalid_at = ? WHERE id = ?" % table, (now, old_id)
        )
        new_id, reason = _insert_row(
            conn, table, {**dict(new_values or {}), "supersedes": old_id, "valid_from": now}, run_id
        )
        if new_id is None:
            conn.execute("ROLLBACK TO ak_supersede")
            conn.execute("RELEASE ak_supersede")
            _refuse(conn, table, run_id, why, reason)
            _finish(conn, owns, True)
            return None
        before = {"old": {"id": old_id, "status": old["status"], "invalid_at": old["invalid_at"]}}
        _event(conn, "supersede", (table, new_id), (run_id, why, before))
        conn.execute("RELEASE ak_supersede")
        _finish(conn, owns, True)
        return new_id
    except _ERRORS:
        logger.info("knowledge db: supersede failed", exc_info=True)
        _safe_rollback(conn, owns)
        return None


def set_status(conn, table: str, row_id: int, status: str, **cols) -> bool:
    """Set a row's status (and any extra columns). True when a row changed."""
    owns = _owns(conn)
    try:
        run_id = str(cols.pop("run_id", "") or "")
        why = cols.pop("why", "")
        if status not in STATUSES or table not in knowledge_schema.LEARNED_TABLES:
            return False
        have = set(knowledge_schema.table_columns(conn, table))
        extra = {k: _dump(v) for k, v in cols.items() if k in have and k not in _NO_WRITE | {"status"}}
        reason = _secret(extra, run_id)
        if reason:
            _refuse(conn, table, run_id, why, "secret " + reason)
            _finish(conn, owns, True)
            return False
        old = conn.execute("SELECT * FROM %s WHERE id = ?" % table, (row_id,)).fetchone()
        if old is None:
            return False
        new = {"status": status, **extra}
        if status in _CLOSED and old["invalid_at"] is None:
            new["invalid_at"] = time.time()
        before = {c: old[c] for c in new}
        sets = ", ".join("%s = ?" % c for c in new)
        conn.execute("UPDATE %s SET %s WHERE id = ?" % (table, sets), list(new.values()) + [row_id])
        _event(conn, "status", (table, row_id), (run_id, why, before))
        _finish(conn, owns, True)
        return True
    except _ERRORS:
        logger.info("knowledge db: set_status failed", exc_info=True)
        _safe_rollback(conn, owns)
        return False


def rows(conn, table: str, where: str = "", args: object = (), limit: int = 100) -> list:
    """Rows of *table* as dicts, newest first. *where* is a constant SQL fragment
    with ``?`` placeholders (never caller text); *args* are bound. Bounded."""
    try:
        if table not in knowledge_schema.LEARNED_TABLES and table != "runs":
            return []
        clause = str(where or "").strip()
        if clause.upper().startswith("WHERE "):
            clause = clause[6:]
        cap = max(1, min(int(limit), knowledge_limits.SNAPSHOT_MAX_ROWS))
        order = "rowid" if table == "runs" else "id"
        sql = "SELECT * FROM %s%s ORDER BY %s DESC LIMIT ?" % (
            table,
            " WHERE " + clause if clause else "",
            order,
        )
        found = conn.execute(sql, list(args or ()) + [cap]).fetchall()
        return [dict(row) for row in found]
    except _ERRORS:
        logger.info("knowledge db: rows failed", exc_info=True)
        return []


_ROWS_CLOSED = "status IN ('superseded', 'retired')"
#: Runs the learner is done with: learned, skipped, or failed with no tries left.
_RUNS_CLOSED = (
    "learn_state IN ('learned', 'skipped') OR (learn_state = 'failed' AND learn_tries >= 3)"
)


def _drop_closed(conn, table: str, rule: tuple, cutoff: float) -> int:
    """Closed rows (``rule`` = where, stamp column) past *cutoff*, then the oldest
    closed rows while the table is over 90% of its cap."""
    closed, stamp = rule
    dropped = conn.execute(
        "DELETE FROM %s WHERE (%s) AND %s < ?" % (table, closed, stamp), (cutoff,)
    ).rowcount
    over = _count(conn, table) - int(knowledge_limits.MAX_ROWS[table] * 0.9)
    if over > 0:
        dropped += conn.execute(
            "DELETE FROM %s WHERE rowid IN (SELECT rowid FROM %s WHERE %s ORDER BY %s LIMIT ?)"
            % (table, table, closed, stamp),
            (over,),
        ).rowcount
    return dropped


def _compact_runs(conn, cutoff: float) -> int:
    """Closed runs first; past the hard cap the oldest open runs go too (never one
    mid-learn), so run starts cannot grow the table without bound. Then the learn
    markers and rollback rows nothing needs any more."""
    dropped = _drop_closed(conn, "runs", (_RUNS_CLOSED, "started"), cutoff)
    over = _count(conn, "runs") - knowledge_limits.MAX_ROWS["runs"]
    if over > 0:
        dropped += conn.execute(
            "DELETE FROM runs WHERE rowid IN (SELECT rowid FROM runs"
            " WHERE learn_state != 'learning' ORDER BY started LIMIT ?)",
            (over,),
        ).rowcount
    _drop_run_markers(conn)
    return dropped


def _drop_run_markers(conn) -> None:
    """A learn marker lives as long as its run row. A rollback row lives while its
    run still has row events it stops ``rollback_run`` undoing twice, and only the
    newest per run counts (``rollback_run`` reads just that one)."""
    conn.execute(
        "DELETE FROM events WHERE event = 'learn_marker'"
        " AND run_id NOT IN (SELECT run_id FROM runs)"
    )
    conn.execute(
        "DELETE FROM events WHERE event = 'rollback' AND (id < (SELECT MAX(r.id) FROM events r"
        " WHERE r.event = 'rollback' AND r.run_id = events.run_id)"
        " OR NOT EXISTS (SELECT 1 FROM events e WHERE e.run_id = events.run_id"
        " AND e.table_name != '' AND e.row_id IS NOT NULL))"
    )


def compact(conn, table: str) -> int:
    """Drop closed rows past retention, then the oldest closed rows if the table is
    still near its cap, so caps never wedge learning. Notes are never compacted.
    Returns rows dropped; never raises."""
    owns = _owns(conn)
    try:
        cutoff = time.time() - knowledge_limits.RETENTION_DAYS * 86400
        conn.execute("DELETE FROM events WHERE table_name = ? AND ts < ?", (table, cutoff))
        if table == "notes" or table not in knowledge_limits.MAX_ROWS:
            _finish(conn, owns, True)
            return 0
        if table == "runs":
            dropped = _compact_runs(conn, cutoff)
        else:
            dropped = _drop_closed(conn, table, (_ROWS_CLOSED, "invalid_at"), cutoff)
        _finish(conn, owns, True)
        return max(0, dropped)
    except _ERRORS:
        logger.info("knowledge db: compact failed", exc_info=True)
        _safe_rollback(conn, owns)
        return 0


def has_marker(conn, run_id: str, stage: str) -> bool:
    """True when *stage* already learned *run_id* (a ``learn_marker`` event)."""
    try:
        return (
            conn.execute(
                "SELECT 1 FROM events WHERE event = 'learn_marker' AND run_id = ? AND detail = ? LIMIT 1",
                (str(run_id), str(stage)),
            ).fetchone()
            is not None
        )
    except sqlite3.Error:
        return False


def put_marker(conn, run_id: str, stage: str) -> bool:
    """Record that *stage* finished for *run_id*. Idempotent; never raises."""
    owns = _owns(conn)
    try:
        if not has_marker(conn, run_id, stage):
            _event(conn, "learn_marker", ("", None), (run_id, stage, None))
        _finish(conn, owns, True)
        return True
    except _ERRORS:
        logger.info("knowledge db: put_marker failed", exc_info=True)
        _safe_rollback(conn, owns)
        return False
