"""Timing row writes (split out of knowledge_stage_timings)."""

from __future__ import annotations

from typing import NamedTuple, cast

from tools.mobile import knowledge_db, knowledge_limits


class Plan(NamedTuple):
    old: dict | None
    vals: dict
    why: str


def status_of(vals: dict) -> str:
    return "active" if vals["n"] >= knowledge_limits.MIN_SAMPLES_WAIT else "candidate"


def insert_row(conn, run_id: str, plan: Plan) -> bool:
    write = knowledge_db.Write(
        {**plan.vals, "status": status_of(plan.vals)}, run_id=run_id, why=plan.why
    )
    return knowledge_db.insert(conn, "timings", write) is not None


def update_row(conn, run_id: str, plan: Plan) -> bool:
    old = cast(dict, plan.old)
    runs = knowledge_db._bump_runs(old.get("runs_json"), run_id)
    cols = {
        k: v
        for k, v in plan.vals.items()
        if k not in ("screen_key", "action", "target")
    }
    return knowledge_db.set_status(
        conn,
        "timings",
        knowledge_db.Status(
            old["id"],
            status_of(plan.vals),
            {"runs_json": runs, **cols},
            run_id=run_id,
            why=plan.why,
        ),
    )
