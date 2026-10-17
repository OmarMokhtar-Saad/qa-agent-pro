"""One flow per goal: a repeated route does not pile up siblings (see ``kstg_flow_prune``).

Another save of the same normalised goal whose route is the new route, or a contiguous
run of it (or the other way round), is a duplicate: the better-ranked one stays
(reached, then longer, then replay score; the newest wins a tie) and the other is
deleted whole, with its packet chain. Counters are not merged. The packets of the save
itself are never each other's duplicates. Never raises.
"""

from __future__ import annotations

import logging

from tools.mobile import (
    app_flows,
    flow_stats,
    kstg_flow_build,
    kstg_flow_chain,
    kstg_flow_rules,
    kstg_flow_sig,
)

logger = logging.getLogger(__name__)


def route_of(template: dict) -> list:
    steps = list(template.get("steps") or [])
    befores = list((template.get("meta") or {}).get("before") or [])
    return kstg_flow_sig.identities(steps, befores)


def _rank(template: dict) -> tuple:
    reached = bool((template.get("meta") or {}).get("reached"))
    return (reached, len(template.get("steps") or []), flow_stats.score(template))


def _same_goal(template: dict, goal: str) -> bool:
    saved = str((template.get("meta") or {}).get("goal") or "")
    return kstg_flow_build.normalise(saved) == kstg_flow_build.normalise(goal)


def _related(a: list, b: list) -> bool:
    return bool(a) and (
        a == b or kstg_flow_rules.inside(a, b) or kstg_flow_rules.inside(b, a)
    )


def _templates(package: str, names: list) -> list:
    got = [(n, app_flows.get_flow(package, n)) for n in names]
    return [(n, g["content"]) for n, g in got if not g.get("error")]


def _losers(items: list, held: list) -> list:
    """Names of the duplicates among the saved *items*; *held* are the new save's flows."""
    mine = [t for n, t in items if n in held]
    if not mine:
        return []
    route = [step for t in mine for step in route_of(t)]
    new = (all(_rank(t)[0] for t in mine), len(route), max(_rank(t)[2] for t in mine))
    goal = str((mine[0].get("meta") or {}).get("goal") or "")
    dupes = [(n, t) for n, t in items if n not in held and _same_goal(t, goal)]
    related = [(n, t) for n, t in dupes if _related(route_of(t), route)]
    if any(_rank(t) > new for _n, t in related):
        return list(held)
    return [n for n, _t in related]


def collapse(package: str, held: list) -> list:
    """Delete the duplicates of the flows *held* (one save); the names removed."""
    removed: list = []
    try:
        rows = app_flows.list_flows(package).get("content") or []
        items = _templates(package, [r["name"] for r in rows])
        losers = _losers(items, list(held))
        keep = set() if set(held) <= set(losers) else set(held)
        chains = kstg_flow_chain.doomed(items, losers, keep)
        for name in dict.fromkeys(losers + chains):
            if not app_flows.delete_flow(package, name).get("error"):
                removed.append(name)
    except Exception:
        logger.exception("flow goal collapse failed")
    return removed
