"""How well a saved flow has replayed: counters, a Wilson score, recency decay.

``meta["stats"] = {runs, ok, seen, last}``: replays tried, replays that landed to the
end, learn runs that produced the same route, and the epoch of the last event. A flow
saved before the counters existed has none; it scores 0 (so only the other ranking
terms decide) and is never pruned for low success. Never raises.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from tools.mobile import app_store

logger = logging.getLogger(__name__)

#: Seconds after which a flow's score halves (30 days).
HALF_LIFE_S = 30 * 86400.0
_Z = 1.96
#: Replays that all failed before a flow that never reached its goal is dead.
DEAD_RUNS = 2
#: Replays that all failed before a flow that once reached its goal is dead too: the
#: route was real once, so it gets more tries, but 0 landed replays in this many means
#: the app moved on (run mrun-20261017-205436: a reached flow at 0 of 11 still picked).
DEAD_RUNS_REACHED = 4
_FIELDS = ("runs", "ok", "seen")
_FLOWS_FILE = "flows.json"


def clean(raw: Any) -> dict:
    """*raw* as ``{runs, ok, seen, last}`` of non-negative numbers, ``{}`` if unusable."""
    if not isinstance(raw, dict):
        return {}
    out = {k: max(0, raw[k]) for k in _FIELDS if type(raw.get(k)) is int}
    if isinstance(raw.get("last"), (int, float)):
        out["last"] = float(raw["last"])
    out["ok"] = min(out.get("ok", 0), out.get("runs", 0))
    return out if any(out.get(k) for k in _FIELDS) else {}


def wilson(ok: int, runs: int) -> float:
    """Lower bound of the 95% Wilson interval for *ok* successes in *runs* tries."""
    if runs <= 0:
        return 0.0
    p = ok / runs
    den = 1 + _Z * _Z / runs
    mid = p + _Z * _Z / (2 * runs)
    span = _Z * math.sqrt(p * (1 - p) / runs + _Z * _Z / (4 * runs * runs))
    return max(0.0, (mid - span) / den)


def decay(last: float, now: float) -> float:
    """1.0 for a fresh event, halving every :data:`HALF_LIFE_S` since *last*."""
    if not last:
        return 1.0
    return 0.5 ** (max(0.0, now - last) / HALF_LIFE_S)


def of(template: dict) -> dict:
    """The cleaned stats of a stored flow template (``{}`` for a legacy flow)."""
    return clean((template.get("meta") or {}).get("stats"))


def score(template: dict, now: float = 0.0) -> float:
    """The ranking score of a flow: Wilson lower bound of replay success, decayed."""
    stats = of(template)
    base = wilson(stats.get("ok", 0), stats.get("runs", 0))
    return base * decay(stats.get("last", 0.0), now or time.time())


def bump(stats: Any, ok: bool, learned: bool = False) -> dict:
    """*stats* after one more replay (``ok`` or not) or one more learn run."""
    got = clean(stats)
    if learned:
        got["seen"] = got.get("seen", 0) + 1
    else:
        got["runs"] = got.get("runs", 0) + 1
        got["ok"] = got.get("ok", 0) + (1 if ok else 0)
    got["last"] = time.time()
    return got


def record(package: str, name: str, ok: bool) -> None:
    """Count one replay of flow *name* in its stored meta (never raises)."""

    def mutate(body: dict) -> str:
        entry = (body.get("flows") or {}).get(name)
        if isinstance(entry, dict) and isinstance(entry.get("meta"), dict):
            entry["meta"]["stats"] = bump(entry["meta"].get("stats"), ok)
        return ""

    try:
        app_store.update_json(package, _FLOWS_FILE, mutate)
    except Exception:
        logger.exception("flow stats record failed")


def counters(template: dict) -> dict:
    """The counters of a flow; one saved before they existed counts as ``runs 0, ok 0``."""
    return {"runs": 0, "ok": 0, **of(template)}


def dead(template: dict) -> bool:
    """A flow that never replayed ok in its dead-run threshold of tries.

    The threshold is ``DEAD_RUNS`` for a flow that never reached its goal and
    ``DEAD_RUNS_REACHED`` for one that did."""
    meta = template.get("meta") or {}
    stats = counters(template)
    limit = DEAD_RUNS_REACHED if meta.get("reached") else DEAD_RUNS
    return stats["ok"] == 0 and stats["runs"] >= limit
