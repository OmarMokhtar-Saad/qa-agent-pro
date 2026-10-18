"""Near-duplicate learned flows (see ``knowledge_stage_flows``).

A new flow whose steps equal, or sit inside, an existing flow of the package adds
nothing, so it is not saved. An existing flow that sits strictly inside the new one is
replaced by it. Steps compare by op, target label and the pressed key or scroll
direction: run ids, waits' ``max_s`` ceilings and screen records do not count. A flow
that stops short of the goal never covers one that reaches it. Never raises.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from tools.mobile import app_flows, app_flows_meta, kstg_flow_packets, kstg_flow_rules

logger = logging.getLogger(__name__)


class Verdict(NamedTuple):
    covered_by: str  # an existing flow that already holds the new one, else ""
    replaces: list  # existing flows the new one strictly contains


def _label(step: dict) -> str:
    target = step.get("target")
    if isinstance(target, dict):
        target = target.get("text") or target.get("label") or target.get("rid")
    name = target or step.get("text") or step.get("label") or step.get("field") or ""
    return "%s:%s" % (name, step.get("key") or step.get("direction") or "")


def normalized(template: dict) -> list:
    """The comparable form of a template's steps: ``op|label`` per step."""
    steps = template.get("steps") or []
    return [
        "%s|%s" % (s.get("op"), _label(s))
        for s in steps
        if isinstance(s, dict) and not str(s.get("op") or "").startswith("wait_")
    ]


def _others(package: str, name: str) -> list:
    rows = app_flows.list_flows(package).get("content") or []
    got = [(r["name"], app_flows.get_flow(package, r["name"])) for r in rows]
    return [(n, g["content"]) for n, g in got if n != name and not g.get("error")]


def judge(package: str, name: str, template: dict) -> Verdict:
    """How *template* (saved as *name*) relates to the package's other flows."""
    new, goal = normalized(template), app_flows_meta.reached(template)
    covered, replaces = "", []
    try:
        for other, content in _others(package, name):
            old = normalized(content)
            holds = old == new or kstg_flow_rules.inside(new, old)
            if holds and (app_flows_meta.reached(content) or not goal):
                covered = covered or other
            elif old == new or kstg_flow_rules.inside(old, new):
                replaces.append(other)
    except Exception:
        logger.exception("flow dedupe failed")
        return Verdict("", [])
    return Verdict(covered, [] if covered else replaces)


def _base(name: str) -> str:
    hit = kstg_flow_packets.PACKET_RE.match(name)
    return hit.group("base") if hit else name


def _chain(package: str, names: list) -> list:
    """*names* plus the other packets of each one's chain (a chain goes as a unit)."""
    bases = {_base(n) for n in names}
    rows = app_flows.list_flows(package).get("content") or []
    mates = [r["name"] for r in rows if _base(r["name"]) in bases]
    return list(dict.fromkeys(list(names) + mates))


def drop(package: str, names: list, keep: str = "") -> list:
    """Delete the flows *names* with their packet chains, never *keep*; those removed."""
    every = _chain(package, names) if names else []
    return [
        n
        for n in every
        if n != keep and not app_flows.delete_flow(package, n).get("error")
    ]
