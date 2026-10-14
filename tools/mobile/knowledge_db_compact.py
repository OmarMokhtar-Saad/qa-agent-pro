"""Retention and compaction of the knowledge db."""

from __future__ import annotations

import logging
import time

from tools.mobile import knowledge_limits
from tools.mobile.knowledge_db_args import Retire
from tools.mobile.knowledge_db_core import ERRORS, count, finish, owns, safe_rollback

logger = logging.getLogger("tools.mobile.knowledge_db")

_ROWS_CLOSED = "status IN ('superseded', 'retired')"
#: Runs the learner is done with: learned, skipped, or failed with no tries left.
_RUNS_CLOSED = "learn_state IN ('learned', 'skipped') OR (learn_state = 'failed' AND learn_tries >= 3)"


def _drop_closed(conn, table: str, r: Retire) -> int:
    """Closed rows past the cutoff, then the oldest closed rows while the table is
    over 90% of its cap."""
    dropped = conn.execute(
        "DELETE FROM %s WHERE (%s) AND %s < ?" % (table, r.closed, r.stamp),
        (r.cutoff,),
    ).rowcount
    over = count(conn, table) - int(knowledge_limits.MAX_ROWS[table] * 0.9)
    if over > 0:
        dropped += conn.execute(
            "DELETE FROM %s WHERE rowid IN (SELECT rowid FROM %s WHERE %s ORDER BY %s LIMIT ?)"
            % (table, table, r.closed, r.stamp),
            (over,),
        ).rowcount
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


def _compact_runs(conn, cutoff: float) -> int:
    """Closed runs first; past the hard cap the oldest open runs go too (never one
    mid-learn), so run starts cannot grow the table without bound. Then the learn
    markers and rollback rows nothing needs any more."""
    dropped = _drop_closed(conn, "runs", Retire(_RUNS_CLOSED, "started", cutoff))
    over = count(conn, "runs") - knowledge_limits.MAX_ROWS["runs"]
    if over > 0:
        dropped += conn.execute(
            "DELETE FROM runs WHERE rowid IN (SELECT rowid FROM runs"
            " WHERE learn_state != 'learning' ORDER BY started LIMIT ?)",
            (over,),
        ).rowcount
    _drop_run_markers(conn)
    return dropped


def _expire_events(conn, table: str, cutoff: float) -> None:
    conn.execute("DELETE FROM events WHERE table_name = ? AND ts < ?", (table, cutoff))


def _compact_table(conn, table: str, cutoff: float) -> int:
    if table == "runs":
        return _compact_runs(conn, cutoff)
    return _drop_closed(conn, table, Retire(_ROWS_CLOSED, "invalid_at", cutoff))


def compact(conn, table: str) -> int:
    """Drop closed rows past retention, then the oldest closed rows if the table is
    still near its cap, so caps never wedge learning. Notes are never compacted.
    Returns rows dropped; never raises."""
    own = owns(conn)
    try:
        cutoff = time.time() - knowledge_limits.RETENTION_DAYS * 86400
        _expire_events(conn, table, cutoff)
        if table == "notes" or table not in knowledge_limits.MAX_ROWS:
            finish(conn, own, True)
            return 0
        dropped = _compact_table(conn, table, cutoff)
        finish(conn, own, True)
        return max(0, dropped)
    except ERRORS:
        logger.info("knowledge db: compact failed", exc_info=True)
        safe_rollback(conn, own)
        return 0
