"""Repairing a route that mixes screen-key families before it is gated.

A degraded ``sk1d`` key beside full ``sk2`` ones used to refuse the whole route
(``kstg_flow_gate.reject``). ``repair`` runs first: it cuts the route at the first
degraded step when the clean prefix still proves the goal (ends on an assertion or
``done``), otherwise blanks the degraded keys (``""`` = no record, which the matcher,
``_still``, the change-wait check and the signature all treat as unknown) and keeps
every step. Pure; never raises; never mutates its input.
"""

from __future__ import annotations

from tools.mobile.kstg_flow_gate import family, mixed_keys

DEGRADED = "sk1d"
MIN_PREFIX = 2
_GOAL_OUTCOMES = ("assert_pass", "done")
_KEYS = ("screen_key", "after_screen_key")


def _is_degraded(rec: dict) -> bool:
    return any(family(rec.get(k)) == DEGRADED for k in _KEYS)


def _first_degraded(recs: list) -> int:
    return next((i for i, r in enumerate(recs) if _is_degraded(r)), len(recs))


def _proves_goal(prefix: list) -> bool:
    last = str(prefix[-1].get("outcome") or "") if prefix else ""
    return len(prefix) >= MIN_PREFIX and last in _GOAL_OUTCOMES


def _blank(rec: dict) -> dict:
    return {
        k: ("" if k in _KEYS and family(v) == DEGRADED else v) for k, v in rec.items()
    }


def repair(recs: list) -> list:
    """*recs* made consistent in one key family; unchanged when already consistent."""
    if not mixed_keys(recs):
        return recs
    prefix = recs[: _first_degraded(recs)]
    if _proves_goal(prefix):
        return prefix
    return [_blank(r) for r in recs]
