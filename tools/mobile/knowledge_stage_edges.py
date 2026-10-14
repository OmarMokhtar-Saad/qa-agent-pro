"""Learning stage edges: ``(before_key, op, target) -> after_key`` with p50/p90 ms.

Descriptive only: an edge is ``active`` and ``learned`` at once and nothing acts
on it; the shortcuts stage and the report read it. Built from consecutive OK
steps of one run. Counts add up across runs; p50/p90 are the count-weighted mean
of each run's own percentiles (exact for a single run). A run already listed in
an edge's ``runs_json`` is skipped, so a repeat is a no-op. At the row cap the
lowest-count edge is retired first. Never raises (the pipeline wraps it too).
"""

from __future__ import annotations

import json
import logging
from typing import Any, NamedTuple

from tools.mobile import knowledge_db, knowledge_limits
from tools.mobile.knowledge_learn import StageResult
from tools.mobile.kstg_edge_snap import snapshot  # noqa: F401

logger = logging.getLogger(__name__)


def ok_steps(steps: Any) -> list:
    """The steps that ended ``ok`` and carry both screen keys."""
    out = []
    for rec in list(steps or []):
        if (
            rec.get("outcome") == "ok"
            and rec.get("screen_key")
            and rec.get("after_screen_key")
        ):
            out.append(rec)
    return out


def percentile(values: list, pct: float) -> int:
    """Nearest-rank percentile of *values* (ints); 0 when empty."""
    ordered = sorted(int(v) for v in values)
    if not ordered:
        return 0
    rank = max(1, -(-int(pct * len(ordered)) // 100))
    return ordered[min(rank, len(ordered)) - 1]


def collect(steps: Any) -> dict:
    """``{(from, op, target, to): [ms, ...]}`` for one run."""
    found: dict = {}
    for rec in ok_steps(steps):
        key = (
            rec["screen_key"],
            rec.get("op", ""),
            rec.get("target_rid", ""),
            rec["after_screen_key"],
        )
        found.setdefault(key, []).append(int(rec.get("ms") or 0))
    return found


def _runs(row: dict) -> list:
    try:
        got = json.loads(row.get("runs_json") or "[]")
        return got if isinstance(got, list) else []
    except ValueError:
        return []


def _blend(old: dict, times: list) -> dict:
    n_old, n_new = int(old.get("count") or 0), len(times)
    total = max(1, n_old + n_new)
    out = {"count": n_new}
    for col, pct in (("p50_ms", 50), ("p90_ms", 90)):
        mixed = (
            int(old.get(col) or 0) * n_old + percentile(times, pct) * n_new
        ) / total
        out[col] = int(round(mixed)) - int(old.get(col) or 0)
    return out


def _make_room(conn, run_id: str) -> None:
    """Retire the lowest-count open edge when the table is at its cap."""
    cap = knowledge_limits.MAX_ROWS["edges"]
    (have,) = conn.execute("SELECT COUNT(*) FROM edges").fetchone()
    if have < cap:
        return
    row = conn.execute(
        "SELECT id FROM edges WHERE invalid_at IS NULL ORDER BY count ASC, id ASC LIMIT 1"
    ).fetchone()
    if row is not None:
        knowledge_db.set_status(
            conn,
            "edges",
            knowledge_db.Status(
                row["id"], "retired", None, run_id=run_id, why="edge cap"
            ),
        )


class EdgeObs(NamedTuple):
    run_id: str
    key: tuple
    times: list


_NATURAL = ("from_key", "action", "target", "to_key")


def _seen(conn, obs: EdgeObs, old: dict) -> int:
    if obs.run_id and obs.run_id in _runs(old):
        return 0
    deltas = _blend(old, obs.times)
    natural = dict(zip(_NATURAL, obs.key, strict=True))
    got = knowledge_db.upsert_counter(
        conn,
        "edges",
        knowledge_db.Counter(natural, deltas, run_id=obs.run_id, why="edge seen"),
    )
    return 1 if got else 0


def _learn(conn, obs: EdgeObs) -> int:
    natural = dict(zip(_NATURAL, obs.key, strict=True))
    _make_room(conn, obs.run_id)
    values = {
        **natural,
        "count": len(obs.times),
        "p50_ms": percentile(obs.times, 50),
        "p90_ms": percentile(obs.times, 90),
        "status": "active",
        "trust": "learned",
    }
    write = knowledge_db.Write(values, run_id=obs.run_id, why="edge learned")
    return 1 if knowledge_db.insert(conn, "edges", write) else 0


def _one(conn, obs: EdgeObs) -> int:
    where = "from_key = ? AND action = ? AND target = ? AND to_key = ? AND invalid_at IS NULL"
    found = knowledge_db.rows(conn, "edges", knowledge_db.Where(where, obs.key, 1))
    if found:
        return _seen(conn, obs, found[0])
    return _learn(conn, obs)


def run(conn, lc) -> StageResult:
    wrote = 0
    seen = collect(lc.steps)
    for key in sorted(seen):
        wrote += _one(conn, EdgeObs(lc.run_id, key, seen[key]))
    return StageResult(
        "edges", wrote=wrote, notes=["%d edge rows touched" % wrote] if wrote else []
    )
