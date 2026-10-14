"""Stage C step 4: retention decay of lessons; shortcuts that failed too often go stale."""

from __future__ import annotations

from typing import Any

from tools.mobile import knowledge_db as kdb
from tools.mobile import knowledge_limits as limits
from tools.mobile.knowledge_gate_core import Transition


def _used(conn: Any, run_id: str, row: Any) -> None:
    fields = {"retention": 1.0, "last_used_run": run_id}
    move = kdb.Status(row["id"], "active", fields, run_id=run_id, why="used")
    kdb.set_status(conn, "lessons", move)


def _decayed(conn: Any, run_id: str, row: Any) -> Transition | None:
    kept = row["retention"] if row["retention"] is not None else 1.0
    value = float(kept) * limits.RETENTION_DECAY
    status = "stale" if value < limits.STALE_RETENTION else "active"
    move = kdb.Status(
        row["id"], status, {"retention": value}, run_id=run_id, why="unused"
    )
    if kdb.set_status(conn, "lessons", move) and status == "stale":
        return Transition(
            "lessons", row["id"], "active", "stale", "unused for too long"
        )
    return None


def _decay(conn: Any, run_id: str, seen: set) -> list:
    """Lessons: used this run (or global) reset; unused decay, stale under the floor."""
    done: list = []
    rows = conn.execute(
        "SELECT * FROM lessons WHERE status = 'active' AND invalid_at IS NULL ORDER BY id"
    ).fetchall()
    for row in rows:
        if not row["screen_key"] or row["screen_key"] in seen:
            _used(conn, run_id, row)
            continue
        got = _decayed(conn, run_id, row)
        if got is not None:
            done.append(got)
    return done


def _failing_shortcuts(conn: Any, run_id: str) -> list:
    done: list = []
    rows = conn.execute(
        "SELECT id FROM shortcuts WHERE status IN ('active', 'candidate') AND invalid_at IS NULL AND fail >= ?",
        (limits.STALE_AFTER_FAILS,),
    ).fetchall()
    for row in rows:
        move = kdb.Status(
            row["id"], "stale", None, run_id=run_id, why="failed too often"
        )
        if kdb.set_status(conn, "shortcuts", move):
            done.append(
                Transition(
                    "shortcuts", row["id"], "active", "stale", "failed too often"
                )
            )
    return done
