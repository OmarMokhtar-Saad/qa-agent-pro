"""Building a saved flow from a clean explore run (see ``knowledge_stage_flows``).

A flow is a template: ``knowledge_trace_flow.flow_action`` already reduced every
step to selectors and field names, so no typed value can reach it.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from tools.mobile import (
    flow_nochange,
    goal_scrub,
    kstg_flow_host,
    kstg_flow_popup,
    kstg_foreign,
    run_store,
    target_pin,
)
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
    goal = goal or kstg_flow_host.goal(run_id, body)
    return goal_scrub.scrub(str(goal or ""), str(body.get("serial") or ""))


def normalise(goal: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", goal.lower()))


def flow_name(goal: str, reserve: int = 0, sig: str = "") -> str:
    """A valid flow name: a short goal slug plus a hash of the route shape *sig*.

    The same route reached under reworded goals is one flow (the goal only labels
    it); without a *sig* the hash covers the normalised goal. *reserve* characters
    stay free for a ``-pN`` packet suffix.
    """
    norm = normalise(goal)
    digest = hashlib.sha1((sig or norm).encode("utf-8")).hexdigest()[:_HASH_CHARS]
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


def _ran(rec: dict, package: str) -> bool:
    """A step that ran; a permission-dialog tap needs no landing (the dialog is left)."""
    landed = rec.get("landed") is not False or kstg_flow_popup.is_popup_tap(
        rec, package
    )
    return (
        bool(rec.get("flow_action"))
        and str(rec.get("outcome") or "") in OK_OUTCOMES
        and landed
        and not _saw_no_change(rec)
    )


def _in_route(rec: dict, package: str) -> bool:
    return kstg_foreign.own(rec, package) or kstg_flow_popup.is_popup_tap(rec, package)


def _first_own(steps: list, package: str) -> int:
    """Index of the first step stamped with *package*; -1 when none is."""
    return next(
        (i for i, r in enumerate(steps) if kstg_foreign.observed(r) == package), -1
    )


def _approach(steps: list, package: str) -> list:
    """The steps on transient surfaces (App Tester, installer) just before the app.

    H20: the route to an app installed during the run starts on those surfaces; it is
    saved under the app so the whole route can be replayed. The run of transient
    screens ends at the first step on any other screen.
    """
    out: list = []
    for rec in reversed(steps[: max(_first_own(steps, package), 0)]):
        if not target_pin.transient(kstg_foreign.observed(rec)):
            break
        out.append(rec)
    return out[::-1]


def app_recs(steps: list, package: str) -> list:
    """The replayable step records of *package* that ran as planned and landed.

    A step that errored, found no element, needed the model, or whose screen never
    changed as expected (``landed`` False) is not part of the route, so it is left out.
    A tap on a popup (an OS permission dialog, or an in-app sheet that came and went)
    stays, as an optional step (``kstg_flow_popup``). The transient screens that led
    to the app (App Tester, installer) stay too (``_approach``).
    """
    lead = [id(r) for r in _approach(steps, package)]
    kept = [
        r
        for r in steps
        if _ran(r, package) and (_in_route(r, package) or id(r) in lead)
    ]
    out: list = []
    for rec in kept:
        rec = kstg_flow_popup.as_optional(rec, package)
        out.append(kstg_flow_popup.as_inapp_optional(out[-1] if out else {}, rec))
    return out


def app_actions(steps: list, package: str) -> list:
    """The replayable actions of *package*'s steps that ran as planned."""
    return [rec["flow_action"] for rec in app_recs(steps, package)]
