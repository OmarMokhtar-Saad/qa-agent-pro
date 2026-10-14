"""The ``runs`` row and ``events`` markers the learning pipeline reads and writes."""

from __future__ import annotations

import json
import logging
import time

from tools.mobile import knowledge_db, knowledge_limits, run_store
from tools.mobile.knowledge_learn_types import MARKER, Outcome
from tools.mobile.knowledge_run_args import RunFacts

logger = logging.getLogger("tools.mobile.knowledge_learn")

_SAVE_SQL = (
    "UPDATE runs SET learn_state = ?, learn_error = ?, summary_json = ?,"
    " learned_at = COALESCE(?, learned_at), verdict = ?, finished = ?"
    " WHERE run_id = ?"
)


def _marked(conn, run_id: str, stage: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM events WHERE event = ? AND run_id = ? AND detail = ? LIMIT 1",
        (MARKER, run_id, stage),
    ).fetchone()
    return row is not None


def _mark(conn, run_id: str, stage: str) -> None:
    conn.execute(
        "INSERT INTO events (ts, event, detail, run_id) VALUES (?, ?, ?, ?)",
        (time.time(), MARKER, stage, run_id),
    )


def _run_row(conn, run_id: str) -> dict:
    row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return dict(row) if row is not None else {}


def _facts(package: str, run_id: str, row: dict) -> RunFacts:
    try:
        env = json.loads(row.get("env_json") or "{}")
    except ValueError:
        env = {}
    return RunFacts(
        run_id,
        package,
        str(row.get("app_version") or ""),
        str(row.get("version_code") or ""),
        env if isinstance(env, dict) else {},
    )


def _verdict(run_id: str) -> dict:
    try:
        body = (run_store.read_manifest(run_id) or {}).get("content") or {}
        final = body.get("final")
        return dict(final) if isinstance(final, dict) else {}
    except Exception:
        logger.exception("knowledge: step failed")
        return {}


def _save(conn, run_id: str, out: Outcome) -> None:
    conn.execute("BEGIN IMMEDIATE")
    learned = time.time() if out.state == "learned" else None
    conn.execute(
        _SAVE_SQL,
        (
            out.state,
            out.error[:300],
            json.dumps(out.summary, sort_keys=True)[:20000],
            learned,
            str(_verdict(run_id).get("outcome") or "")[:40],
            time.time(),
            run_id,
        ),
    )
    conn.commit()


def _compact(conn) -> None:
    for table in knowledge_limits.MAX_ROWS:
        if table == "runs":
            continue
        try:
            knowledge_db.compact(conn, table)
        except Exception:
            logger.exception("knowledge compact failed for %s", table)


def _prior_summary(row: dict) -> dict:
    try:
        got = json.loads(row.get("summary_json") or "{}")
        return got if isinstance(got, dict) else {}
    except ValueError:
        return {}


def _ensure(conn, run_id: str) -> dict:
    """Make sure the ``runs`` row exists and return it."""
    conn.execute(
        "INSERT OR IGNORE INTO runs (run_id, started) VALUES (?, ?)",
        (run_id, time.time()),
    )
    conn.commit()
    return _run_row(conn, run_id)


def _begin(conn, run_id: str) -> None:
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "UPDATE runs SET learn_state = 'learning', learn_tries = learn_tries + 1"
        " WHERE run_id = ?",
        (run_id,),
    )
    conn.commit()
