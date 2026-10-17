"""Which saved flows are worth less than another (see ``kstg_flow_prune``).

Three rules on top of the "stops short of the new route" one: low success (3 or more
replays and a Wilson lower bound under 0.2), stale (no replay or learn run for 90 days),
and dominated duplicates (the same route signature as a better-scored flow, or a flow
that never reached its goal whose steps sit inside one that did). A flow saved before
the counters existed has no stats: it is never dropped for low success or age. Pure.
"""

from __future__ import annotations

import json
import time

from tools.mobile import flow_stats, kstg_flow_sig

MIN_RUNS = 3
MIN_WILSON = 0.2
STALE_S = 90 * 86400.0


def _low(template: dict) -> bool:
    stats = flow_stats.counters(template)
    runs = stats.get("runs", 0)
    return runs >= MIN_RUNS and flow_stats.wilson(stats.get("ok", 0), runs) < MIN_WILSON


def _old(template: dict, now: float) -> bool:
    last = flow_stats.of(template).get("last", 0.0)
    return bool(last) and now - last > STALE_S


def _steps(template: dict) -> list:
    return [json.dumps(s, sort_keys=True) for s in template.get("steps") or []]


def inside(small: list, big: list) -> bool:
    """Whether *small* is a shorter contiguous run of *big*."""
    n = len(small)
    return 0 < n < len(big) and any(big[i : i + n] == small for i in range(len(big)))


def _goal(template: dict) -> bool:
    return bool((template.get("meta") or {}).get("reached"))


def _better(a: tuple, b: tuple) -> bool:
    """Whether (name, template) *a* outranks *b*: reached, score, then name."""
    key = lambda x: (_goal(x[1]), flow_stats.score(x[1]), x[0])  # noqa: E731
    return key(a) > key(b)


def _dominated(item: tuple, others: list) -> bool:
    name, template = item
    sig, steps = kstg_flow_sig.of_template(template), _steps(template)
    for other in others:
        if other[0] == name:
            continue
        if kstg_flow_sig.of_template(other[1]) == sig and _better(other, item):
            return True
        if not _goal(template) and _goal(other[1]) and inside(steps, _steps(other[1])):
            return True
    return False


def doomed(items: list, now: float = 0.0) -> list:
    """Names of the ``(name, template)`` *items* a rule drops."""
    moment = now or time.time()
    return [
        item[0]
        for item in items
        if _low(item[1]) or _old(item[1], moment) or _dominated(item, items)
    ]
