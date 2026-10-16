"""Building a saved flow from a clean explore run (see ``knowledge_stage_flows``).

A flow is a template: ``knowledge_trace_flow.flow_action`` already reduced every
step to selectors and field names, so no typed value can reach it.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from tools.mobile import flow_nochange, kstg_foreign, run_store
from tools.mobile.app_flows_consts import MAX_FLOW_NAME_CHARS, OK_OUTCOMES

MIN_STEPS = 2
_ENDED_OK = tuple(OK_OUTCOMES) + ("done",)
_HASH_CHARS = 8


def _manifest(run_id: str) -> dict:
    body = (run_store.read_manifest(run_id) or {}).get("content")
    return body if isinstance(body, dict) else {}


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def reached(run_id: str) -> str:
    """The goal of an explore run that reached it, else ``""``."""
    body = _manifest(run_id)
    explore = _dict(body.get("explore"))
    passed = _dict(body.get("final")).get("outcome") == "pass"
    if explore.get("stop") != "goal_reached" and not passed:
        return ""
    goal = explore.get("goal") or _dict(explore.get("charter")).get("goal")
    return " ".join(str(goal or "").split())


def normalise(goal: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", goal.lower()))


def flow_name(goal: str, reserve: int = 0, sig: str = "") -> str:
    """A valid flow name keyed by the normalised goal (and route signature *sig*).

    A short slug plus a hash of goal and route: the same goal reached by the same
    route is the same flow, a different route a different one. *reserve* characters
    stay free for a ``-pN`` packet suffix.
    """
    norm = normalise(goal)
    digest = hashlib.sha1((norm + sig).encode("utf-8")).hexdigest()[:_HASH_CHARS]
    room = MAX_FLOW_NAME_CHARS - _HASH_CHARS - 1 - reserve
    slug = norm.replace(" ", "-")[:room].strip("-") or "goal"
    return "%s-%s" % (slug, digest)


def ended_clean(steps: list) -> bool:
    """True when the last recorded step ran as planned (no unresolved error)."""
    return bool(steps) and str(steps[-1].get("outcome") or "") in _ENDED_OK


def _saw_no_change(rec: dict) -> bool:
    """True for a change-wait whose screen key did not move (nothing to replay)."""
    op = _dict(rec.get("flow_action")).get("op")
    return flow_nochange.no_change(
        op, rec.get("screen_key"), rec.get("after_screen_key")
    )


def app_recs(steps: list, package: str) -> list:
    """The replayable step records of *package* that ran as planned and landed.

    A step that errored, found no element, needed the model, or whose screen never
    changed as expected (``landed`` False) is not part of the route, so it is left out.
    """
    return [
        rec
        for rec in steps
        if rec.get("flow_action")
        and kstg_foreign.own(rec, package)
        and str(rec.get("outcome") or "") in OK_OUTCOMES
        and rec.get("landed") is not False
        and not _saw_no_change(rec)
    ]


def app_actions(steps: list, package: str) -> list:
    """The replayable actions of *package*'s steps that ran as planned."""
    return [rec["flow_action"] for rec in app_recs(steps, package)]
