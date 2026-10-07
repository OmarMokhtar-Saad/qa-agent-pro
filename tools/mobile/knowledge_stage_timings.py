"""Learning stage timings: how long a step really takes, per (screen, op, target).

Only OK steps count (no retries, no failures, no guard waits), so the number is
the app's own settle time. Samples are appended (bounded), the p90 is computed
here, and ``wait_ms`` is the bounded wait the executor plug may add before the
step once enough runs agree. Mechanical: it can only lengthen a bounded wait,
so it is active from ``MIN_SAMPLES_WAIT`` samples and the gate never touches it.

Idempotent: a run already in a row's ``runs_json`` adds nothing. Writes go
through ``knowledge_db`` only; no model call.
"""

from __future__ import annotations

import json
import logging
import math

from tools.mobile import knowledge_db, knowledge_limits, run_store
from tools.mobile.knowledge_learn import StageResult

logger = logging.getLogger(__name__)

#: Ops whose duration is not the app's response time.
_SKIP_OPS = frozenset({"wait", "done", "ask_tester", "assert"})
_P90 = 0.9
_OPEN = "screen_key = ? AND action = ? AND target = ? AND invalid_at IS NULL"


def p90(values: list) -> int:
    """Nearest-rank 90th percentile of *values* (ms); 0 for none."""
    ordered = sorted(int(v) for v in values)
    if not ordered:
        return 0
    return ordered[max(0, math.ceil(_P90 * len(ordered)) - 1)]


def wait_for(p90_ms: int, n: int) -> int:
    """The bounded wait for a p90, or 0 while there are too few samples."""
    from tools.mobile import actions

    if n < knowledge_limits.MIN_SAMPLES_WAIT or p90_ms <= 0:
        return 0
    wanted = math.ceil(p90_ms * knowledge_limits.WAIT_P90_MARGIN)
    wanted = min(int(actions.MAX_WAIT_MS), wanted)
    return max(knowledge_limits.ADAPT_WAIT_FLOOR_MS, wanted)


def _factor(run_id: str) -> float:
    """The run's recorded device latency factor (>= 1), read-only; 1 if none."""
    try:
        got = run_store.read_manifest(run_id) or {}
        body = got.get("content") if isinstance(got, dict) else None
        body = body if isinstance(body, dict) else {}
        raw = body.get("latency_factor") or (body.get("knowledge") or {}).get(
            "latency_factor"
        )
        return max(1.0, float(raw)) if raw else 1.0
    except (TypeError, ValueError, AttributeError, OSError):
        return 1.0


def _usable(rec: dict) -> bool:
    return (
        rec.get("outcome") == "ok"
        and not rec.get("retries")
        and not rec.get("fired")
        and int(rec.get("ms") or 0) > 0
        and rec.get("op") not in _SKIP_OPS
        and bool(rec.get("screen_key"))
    )


def collect(steps: list, factor: float = 1.0) -> dict:
    """``{(screen_key, op, target_rid): [ms, ...]}`` of the OK steps, in order."""
    out: dict = {}
    for rec in steps:
        if not _usable(rec):
            continue
        key = (rec["screen_key"], rec.get("op", ""), rec.get("target_rid", ""))
        out.setdefault(key, []).append(max(1, int(round(int(rec["ms"]) / factor))))
    return out


def _samples(raw: object) -> list:
    try:
        got = json.loads(str(raw or "[]"))
        return [int(v) for v in got if isinstance(v, (int, float))]
    except (ValueError, TypeError, OverflowError):
        return []


def _fresh(old: dict | None, run_id: str) -> bool:
    if old is None:
        return True
    try:
        return run_id not in json.loads(str(old.get("runs_json") or "[]"))
    except (ValueError, TypeError, OverflowError):
        return True


def _values(key: tuple, samples: list, version: str) -> dict:
    n = len(samples)
    return {
        "screen_key": key[0],
        "action": key[1],
        "target": key[2],
        "samples_json": samples,
        "n": n,
        "p90_ms": p90(samples),
        "wait_ms": wait_for(p90(samples), n),
        "app_version": version,
    }


def _store(conn, lc, key: tuple, ms: list) -> bool:
    found = knowledge_db.rows(conn, "timings", _OPEN, key, 1)
    old = found[0] if found else None
    if not _fresh(old, lc.run_id):
        return False
    version = str(getattr(lc.facts, "app_version", "") or "")
    samples = ((_samples(old.get("samples_json")) if old else []) + ms)[
        -knowledge_limits.SAMPLES_PER_TIMING :
    ]
    vals = _values(key, samples, version)
    status = "active" if vals["n"] >= knowledge_limits.MIN_SAMPLES_WAIT else "candidate"
    why = "timings %s/%s" % (key[1], key[2])
    if old is None:
        got = knowledge_db.insert(
            conn, "timings", {**vals, "status": status}, run_id=lc.run_id, why=why
        )
        return got is not None
    runs = knowledge_db._bump_runs(old.get("runs_json"), lc.run_id)
    cols = {
        k: v for k, v in vals.items() if k not in ("screen_key", "action", "target")
    }
    return knowledge_db.set_status(
        conn,
        "timings",
        old["id"],
        status,
        runs_json=runs,
        run_id=lc.run_id,
        why=why,
        **cols,
    )


def run(conn, lc) -> StageResult:
    res = StageResult("timings")
    try:
        groups = collect(list(lc.steps or []), _factor(lc.run_id))
        for key, ms in groups.items():
            if _store(conn, lc, key, ms):
                res.wrote += 1
        if groups:
            res.notes.append("%d timing keys seen" % len(groups))
    except Exception as exc:
        logger.exception("knowledge timings stage failed")
        res.error = (type(exc).__name__ + ": " + str(exc))[:200]
    return res
