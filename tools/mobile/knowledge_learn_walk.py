"""Walking ``STAGES`` over one run: one transaction per stage."""

from __future__ import annotations

import importlib
import logging
import time
from typing import Any

from tools.mobile import knowledge_trace, knowledge_versions
from tools.mobile.knowledge_learn_rows import (
    _facts,
    _mark,
    _marked,
    _prior_summary,
    _verdict,
)
from tools.mobile.knowledge_learn_types import STAGES, LearnCtx, StageResult
from tools.mobile.knowledge_run_args import RunKey

logger = logging.getLogger("tools.mobile.knowledge_learn")


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


def _ctx(conn, key: RunKey, row: dict) -> LearnCtx:
    return LearnCtx(
        key.package,
        key.run_id,
        _facts(key.package, key.run_id, row),
        knowledge_trace.load_steps(key.run_id),
        _verdict(key.run_id),
        conn,
        time.time(),
    )


def _entry(conn, name: str, got: StageResult) -> dict:
    out: dict[str, Any] = {name: {"wrote": got.wrote, "notes": list(got.notes)[:10]}}
    if name == "versions":
        out["recheck"] = knowledge_versions.recheck_total(conn)
    return out


def _walk(conn, key: RunKey, row: dict) -> tuple[dict, str]:
    summary = _prior_summary(row)
    lc = _ctx(conn, key, row)
    errors: list = []
    for name in STAGES:
        if _marked(conn, key.run_id, name):
            continue
        got = _transact(conn, name, lc)
        summary.update(_entry(conn, name, got))
        if got.error:
            errors.append(name + ": " + got.error)
    return summary, "; ".join(errors)
