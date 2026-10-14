"""Stage B, the answer side: one transaction that applies a reflection and re-runs the gate."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Any

from tools.mobile import knowledge_db as kdb
from tools.mobile import knowledge_gate
from tools.mobile.knowledge_reflect_ops import _ERRORS, _apply, _clean_ops
from tools.untrusted import wrap_untrusted

logger = logging.getLogger(__name__)


@dataclass
class Job:
    """A validated answer: the ops to apply and how many invalid ones were dropped."""

    package: str
    run_id: str
    ops: list
    dropped: int


def _reply(text: str, error: str = "") -> dict:
    return {
        "error": error or None,
        "content": wrap_untrusted("knowledge reflection", text, limit=2000),
    }


def _screen(package: str, run_id: str, answer: Any) -> Job | dict:
    """The validated job, or the reply that refuses the answer."""
    try:
        ops, dropped, secret = _clean_ops(answer)
    except (ValueError, AttributeError, TypeError):
        return _reply(
            "answer is not valid JSON of the response_schema", "invalid answer"
        )
    if secret:
        return _reply(
            "refused: the answer holds a secret (%s); nothing was saved" % secret,
            "secret refused",
        )
    return Job(package, run_id, ops, dropped)


def _state(conn: Any, run_id: str) -> str:
    row = conn.execute(
        "SELECT reflect_state FROM runs WHERE run_id = ?", (str(run_id),)
    ).fetchone()
    return row["reflect_state"] if row is not None else ""


def _not_pending(conn: Any, run_id: str) -> dict | None:
    """The reply that ends the transaction early, or None when the run is pending."""
    state = _state(conn, run_id)
    if state == "answered":
        conn.rollback()
        return _reply("already answered; nothing changed")
    if state != "pending":
        conn.rollback()
        return _reply("no reflection is pending for this run", "not pending")
    return None


def _run(conn: Any, job: Job) -> dict:
    conn.execute("BEGIN IMMEDIATE")
    early = _not_pending(conn, job.run_id)
    if early is not None:
        return early
    applied = sum(1 for op in job.ops if _apply(conn, op, str(job.run_id)))
    conn.execute(
        "UPDATE runs SET reflect_state = 'answered' WHERE run_id = ?",
        (str(job.run_id),),
    )
    knowledge_gate.gate(conn, str(job.run_id))
    conn.commit()
    return _reply("applied %d op(s), dropped %d invalid" % (applied, job.dropped))


def _abort(conn: Any) -> None:
    try:
        conn.rollback()
    except sqlite3.Error:
        pass


def answer(package: str, run_id: str, given: Any) -> dict:
    """Apply the tester model's answer for *run_id*; never raises."""
    conn = None
    try:
        job = _screen(package, run_id, given)
        if isinstance(job, dict):
            return job
        conn = kdb.open_rw(package)
        if conn is None:
            return _reply("the app knowledge store is unavailable", "store unavailable")
        return _run(conn, job)
    except _ERRORS:
        logger.exception("knowledge reflect apply failed")
        if conn is not None:
            _abort(conn)
        return _reply("could not apply the answer", "apply failed")
    finally:
        if conn is not None:
            conn.close()
