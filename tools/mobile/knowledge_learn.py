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

import importlib
import json
import logging
import time
from dataclasses import dataclass, field

from tools.mobile import (
    knowledge_db,
    knowledge_limits,
    knowledge_run,
    knowledge_trace,
    knowledge_versions,
    run_store,
)

logger = logging.getLogger(__name__)

STAGES = (
    "timings",
    "locators",
    "popups",
    "edges",
    "shortcuts",
    "mistakes",
    "versions",
    "gate",
)

MARKER = "learn_marker"


@dataclass
class StageResult:
    stage: str
    wrote: int = 0
    notes: list = field(default_factory=list)
    error: str = ""


@dataclass
class LearnCtx:
    package: str
    run_id: str
    facts: object
    steps: list
    verdict: dict
    conn: object
    now: float


def run_stage(name: str, conn: object, lc: LearnCtx) -> StageResult:
    """Run ``knowledge_stage_<name>.run(conn, lc)``; never raises."""
    try:
        mod = importlib.import_module("tools.mobile.knowledge_stage_" + str(name))
        got = mod.run(conn, lc)
        if isinstance(got, StageResult):
            return got
        return StageResult(name, error="stage returned no StageResult")
    except Exception as exc:
        logger.exception("knowledge stage %s failed", name)
        return StageResult(name, error=(type(exc).__name__ + ": " + str(exc))[:200])


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


def _transact(conn, name: str, lc: LearnCtx) -> StageResult:
    """One stage, one BEGIN IMMEDIATE; marker on success, rollback on error."""
    try:
        conn.execute("BEGIN IMMEDIATE")
        got = run_stage(name, conn, lc)
        if got.error:
            conn.rollback()
            return got
        _mark(conn, lc.run_id, name)
        conn.commit()
        return got
    except Exception as exc:
        logger.exception("knowledge stage %s transaction failed", name)
        try:
            conn.rollback()
        except Exception:
            logger.exception("knowledge: step failed")
        return StageResult(name, error=(type(exc).__name__ + ": " + str(exc))[:200])


def _run_row(conn, run_id: str) -> dict:
    row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return dict(row) if row is not None else {}


def _facts(package: str, run_id: str, row: dict) -> knowledge_run.RunFacts:
    try:
        env = json.loads(row.get("env_json") or "{}")
    except ValueError:
        env = {}
    return knowledge_run.RunFacts(
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


def _save(conn, run_id: str, state: str, error: str, summary: dict) -> None:
    conn.execute("BEGIN IMMEDIATE")
    learned = time.time() if state == "learned" else None
    conn.execute(
        "UPDATE runs SET learn_state = ?, learn_error = ?, summary_json = ?,"
        " learned_at = COALESCE(?, learned_at), verdict = ?, finished = ?"
        " WHERE run_id = ?",
        (
            state,
            error[:300],
            json.dumps(summary, sort_keys=True)[:20000],
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


def _walk(conn, package: str, run_id: str, row: dict) -> tuple[dict, str]:
    summary = _prior_summary(row)
    lc = LearnCtx(
        package,
        run_id,
        _facts(package, run_id, row),
        knowledge_trace.load_steps(run_id),
        _verdict(run_id),
        conn,
        time.time(),
    )
    errors: list = []
    for name in STAGES:
        if _marked(conn, run_id, name):
            continue
        got = _transact(conn, name, lc)
        summary[name] = {"wrote": got.wrote, "notes": list(got.notes)[:10]}
        if name == "versions":
            summary["recheck"] = knowledge_versions.recheck_total(conn)
        if got.error:
            errors.append(name + ": " + got.error)
    return summary, "; ".join(errors)


def learn_run(package: str, run_id: str) -> dict:
    """Learn from one finished run. ``{"state", "error"?, "noop"?}``. Never raises."""
    conn = None
    try:
        conn = knowledge_db.open_rw(package)
        if conn is None:
            return {"state": "failed", "error": "no knowledge store"}
        conn.execute("INSERT OR IGNORE INTO runs (run_id, started) VALUES (?, ?)", (run_id, time.time()))
        conn.commit()
        row = _run_row(conn, run_id)
        if row.get("learn_state") in ("learned", "skipped"):
            return {"state": row["learn_state"], "noop": True}
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE runs SET learn_state = 'learning', learn_tries = learn_tries + 1"
            " WHERE run_id = ?",
            (run_id,),
        )
        conn.commit()
        summary, error = _walk(conn, package, run_id, row)
        state = "failed" if error else "learned"
        _compact(conn)
        _save(conn, run_id, state, error, summary)
        return {"state": state, "error": error, "summary": summary}
    except Exception as exc:
        logger.exception("knowledge learn_run failed")
        msg = (type(exc).__name__ + ": " + str(exc))[:200]
        try:
            knowledge_run.mark_state(package, run_id, "failed", msg)
        except Exception:
            logger.exception("knowledge: step failed")
        return {"state": "failed", "error": msg}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                logger.exception("knowledge: step failed")
