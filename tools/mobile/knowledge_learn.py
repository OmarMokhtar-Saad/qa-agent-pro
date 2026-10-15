"""The learning pipeline: ``learn_run`` walks ``STAGES`` over one finished run.

Synchronous; the runner calls it in a worker thread AFTER the run result was
returned. Nothing here runs inside a step. Idempotent: a ``learn_marker`` event
per (run_id, stage) means a stage that finished is skipped on a repeat call,
and a run already ``learned`` is a no-op. Each stage runs in ONE
``BEGIN IMMEDIATE`` transaction (marker included), so a failed stage writes
nothing and is retried by the next resume. A missing or crashing stage is an
error in its result only; the next stage still runs. Never raises.
"""

from __future__ import annotations

import importlib  # noqa: F401
import json  # noqa: F401
import logging
import time  # noqa: F401
from dataclasses import dataclass, field  # noqa: F401

from tools.mobile import (
    knowledge_db,
    knowledge_limits,  # noqa: F401
    knowledge_run,
    knowledge_run_store,
    knowledge_trace,  # noqa: F401
    knowledge_versions,  # noqa: F401
    kstg_foreign,
    run_store,  # noqa: F401
)
from tools.mobile.knowledge_learn_rows import (  # noqa: F401
    _begin,
    _compact,
    _ensure,
    _facts,
    _mark,
    _marked,
    _prior_summary,
    _run_row,
    _save,
    _verdict,
)
from tools.mobile.knowledge_learn_types import (  # noqa: F401
    MARKER,
    STAGES,
    LearnCtx,
    Outcome,
    StageResult,
)
from tools.mobile.knowledge_learn_walk import (  # noqa: F401
    _transact,
    _walk,
    run_stage,
)
from tools.mobile.knowledge_run_args import RunKey

logger = logging.getLogger(__name__)


def _learn(conn, key: RunKey) -> dict:
    row = _ensure(conn, key.run_id)
    if row.get("learn_state") in ("learned", "skipped"):
        return {"state": row["learn_state"], "noop": True}
    steps = knowledge_trace.load_steps(key.run_id)
    why = kstg_foreign.package_problem(key.package, steps)
    if why:
        return _finish(conn, key, Outcome("skipped", why, {"skipped": why}))
    _begin(conn, key.run_id)
    summary, error = _walk(conn, key, row)
    _compact(conn)
    return _finish(conn, key, Outcome("failed" if error else "learned", error, summary))


def _finish(conn, key: RunKey, out: Outcome) -> dict:
    _save(conn, key.run_id, out)
    knowledge_run_store.store_state(key.run_id, out.state)
    return {"state": out.state, "error": out.error, "summary": out.summary}


def _failed(key: RunKey, exc: Exception) -> dict:
    msg = (type(exc).__name__ + ": " + str(exc))[:200]
    try:
        knowledge_run.mark_state(key, "failed", msg)
    except Exception:
        logger.exception("knowledge: step failed")
    return {"state": "failed", "error": msg}


def _close(conn) -> None:
    if conn is not None:
        try:
            conn.close()
        except Exception:
            logger.exception("knowledge: step failed")


def learn_run(package: str, run_id: str) -> dict:
    """Learn from one finished run. ``{"state", "error"?, "noop"?}``. Never raises."""
    conn = None
    key = RunKey(package, run_id)
    try:
        conn = knowledge_db.open_rw(package)
        if conn is None:
            return {"state": "failed", "error": "no knowledge store"}
        return _learn(conn, key)
    except Exception as exc:
        logger.exception("knowledge learn_run failed")
        return _failed(key, exc)
    finally:
        _close(conn)
