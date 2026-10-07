"""Executor plug timings: the learned adaptive wait before a step.

``step_wait`` is a dict lookup in ``ctx.knowledge["snap"]["timings"]`` (loaded
once per run by ``snapshot``), keyed ``(screen_key, op, target_rid)``. ZERO I/O
on the hot path; absent => 0. The executor caps the result by MAX_WAIT_MS and
the remaining script budget; this module caps it again so it never lies.
"""

from __future__ import annotations

import logging

from tools.mobile import knowledge_db, knowledge_limits, screen_key

logger = logging.getLogger(__name__)

PRIORITY = 50


def step_op_rid(step: object) -> tuple:
    """``(op, target_rid)`` of an action object or dict, as the trace records them."""
    if isinstance(step, dict):
        op, rid, target = step.get("op"), step.get("rid"), step.get("target")
    else:
        op = getattr(step, "op", "")
        rid = getattr(step, "rid", "")
        target = getattr(step, "target", None)
    if not rid and target is not None:
        rid = (
            target.get("rid")
            if isinstance(target, dict)
            else getattr(target, "rid", "")
        )
    return str(op or ""), str(rid or "")[:200]


def snapshot(conn, facts) -> dict:
    """``{"timings": {(screen_key, op, target): wait_ms}}`` of the active rows."""
    out: dict = {}
    rows = knowledge_db.rows(
        conn,
        "timings",
        "status = 'active' AND invalid_at IS NULL AND n >= ? AND wait_ms > 0",
        (knowledge_limits.MIN_SAMPLES_WAIT,),
        knowledge_limits.SNAPSHOT_MAX_ROWS,
    )
    for row in rows:
        out[(row["screen_key"], row["action"], row["target"])] = int(row["wait_ms"])
    return {"timings": out}


def step_wait(ctx, step, screen) -> int:
    """The learned wait in ms for this step on this screen; 0 when none."""
    try:
        snap = (getattr(ctx, "knowledge", None) or {}).get("snap") or {}
        table = snap.get("timings") or {}
        if not table:
            return 0
        from tools.mobile import actions

        op, rid = step_op_rid(step)
        key = screen_key.screen_key(
            screen,
            str(getattr(ctx, "package", "") or ""),
            str(getattr(ctx, "activity", "") or ""),
        ).key
        wait = int(table.get((key, op, rid), 0) or 0)
        return max(0, min(wait, int(actions.MAX_WAIT_MS)))
    except Exception:
        logger.exception("knowledge timings step_wait failed")
        return 0
