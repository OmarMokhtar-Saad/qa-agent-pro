"""The learn state of a run (``runs.learn_state``) and the runs still to learn."""

from __future__ import annotations

import logging
import time

from tools.mobile import knowledge_db, knowledge_limits, knowledge_run_store, run_store
from tools.mobile.knowledge_run_args import STATES, Move, RunKey

logger = logging.getLogger("tools.mobile.knowledge_run")


def _upsert_state(conn, run_id: str, move: Move) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "INSERT OR IGNORE INTO runs (run_id, started) VALUES (?, ?)",
        (run_id, time.time()),
    )
    tries = ", learn_tries = learn_tries + 1" if move.state == "learning" else ""
    learned = ", learned_at = %f" % time.time() if move.state == "learned" else ""
    conn.execute(
        "UPDATE runs SET learn_state = ?, learn_error = ?%s%s WHERE run_id = ?"
        % (tries, learned),
        (move.state, str(move.error or "")[:300], run_id),
    )
    conn.commit()


def _rollback(conn) -> None:
    try:
        conn.rollback()
    except Exception:
        logger.exception("knowledge: step failed")


def mark_state(key: RunKey, state: str, error: str = "") -> bool:
    """Set ``runs.learn_state``. ``learning`` counts a try. Never raises."""
    if state not in STATES:
        return False
    conn = knowledge_db.open_rw(key.package)
    if conn is None:
        return False
    try:
        _upsert_state(conn, key.run_id, Move(state, error))
        knowledge_run_store.store_state(key.run_id, state)
        return True
    except Exception:
        logger.exception("knowledge run: mark_state failed")
        _rollback(conn)
        return False
    finally:
        conn.close()


def run_finished(run_id: str, started: float, now: float) -> bool:
    """A run is finished when its manifest records ``final``, or it began more
    than STALE_RUN_S ago (abandoned runs never get a ``final``)."""
    try:
        body = (run_store.read_manifest(run_id) or {}).get("content")
        if isinstance(body, dict) and isinstance(body.get("final"), dict):
            return True
    except Exception:
        logger.exception("knowledge: step failed")
    return bool(started) and now - float(started) > knowledge_limits.STALE_RUN_S


def _candidates(conn, skip: set) -> list[str]:
    rows = conn.execute(
        "SELECT run_id, started FROM runs WHERE learn_state = 'unlearned'"
        " OR (learn_state = 'failed' AND learn_tries < 3) ORDER BY started"
    ).fetchall()
    now = time.time()
    return [
        r[0] for r in rows if r[0] not in skip and run_finished(r[0], r[1] or 0.0, now)
    ]


def pending_runs(package: str, *, exclude=(), limit: int | None = None) -> list[str]:
    """Run ids to learn: unlearned, or failed with fewer than 3 tries, and
    finished. Oldest first. Never raises."""
    cap = knowledge_limits.PENDING_RESUME_LIMIT if limit is None else int(limit)
    conn = knowledge_db.open_rw(package)
    if conn is None:
        return []
    try:
        skip = {str(x) for x in (exclude or ())}
        return _candidates(conn, skip)[: max(0, cap)]
    except Exception:
        logger.exception("knowledge run: pending_runs failed")
        return []
    finally:
        conn.close()
