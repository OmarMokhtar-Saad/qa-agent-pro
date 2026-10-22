"""Which saved flows the host lane may auto-replay for ONE case, and whether the user said no.

B17: a flow matches on app + first screen key. Its goal text (``meta.goal``) keeps it out
of auto-replay only when it shares almost no meaningful word with the run or case goal
(M-01; stop words are ignored and ``log in`` / ``sign in`` / ``login`` count as one word, so a
reworded goal still finds it). A skipped flow is named in the replay note and the host may run
it with ``flow=<name>``. What does stop the host lane is the user's own words refusing saved flows. No app or label is named here.
"""

from __future__ import annotations

import logging

from tools.mobile import app_flows, flow_goal_fit, flow_replay_state, run_store

logger = logging.getLogger(__name__)


def title(run_id: str, tc_id: str) -> str:
    """The title of case *tc_id* of the run, or ``""`` (never raises)."""
    try:
        rows = (run_store.list_cases(run_id) or {}).get("content") or []
        return next(
            (str(r.get("title") or "") for r in rows if r.get("tc_id") == tc_id), ""
        )
    except Exception:
        logger.exception("mobile.host_flow_goal.title failed")
        return ""


def declined(run_id: str, goal: str) -> bool:
    """True when the user's own words (the run goal or this case's title) refuse saved flows."""
    asked = flow_replay_state.user_declined
    return asked(goal) or asked(flow_goal_fit.run_goal(run_id))


#: A flow with a goal text needs this share (fifths) of its goal's word stems in the run's goal.
MIN_FIT = 2


def _rows(package: str) -> list:
    return (app_flows.list_flows(package).get("content")) or []


def foreign(package: str, goals: tuple) -> tuple:
    """Names of the flows saved for another goal (M-01): too little goal overlap.

    A flow without a goal text (or with no meaningful word in it, L2) is kept; with no run goal known nothing is dropped.
    """
    known = tuple(g for g in goals if g)
    if not known:
        return ()
    return tuple(
        r["name"]
        for r in _rows(package)
        if flow_goal_fit.has_stems(r.get("goal"))
        and max(flow_goal_fit.fit(g, r["goal"]) for g in known) < MIN_FIT
    )


def _cached(package: str, body: dict, run_id: str) -> tuple:
    """The foreign flows of *package*, read once per saved-flow set (L3) and kept in *body*."""
    seen = body.setdefault("foreign", {})
    sig = tuple((r.get("name"), r.get("goal")) for r in _rows(package))
    if package not in seen or seen[package][0] != sig:
        goals = (flow_goal_fit.run_goal(run_id), str(body.get("goal") or ""))
        seen[package] = (sig, foreign(package, goals))
    return seen[package][1]


def unnamed(body: dict, names: list) -> list:
    """*names* not yet put in a packet note this case (each is named once, L1)."""
    named = body.setdefault("named", set())
    fresh = [n for n in names if n not in named]
    named.update(fresh)
    return fresh


def tried(body: dict) -> tuple:
    """Flows already tried this case."""
    return tuple((body.get("auto_replay") or {}).get("done") or ())


def skipped(package: str, body: dict, run_id: str) -> tuple:
    """Flows left out of auto-replay as saved for another goal; the host may still name one."""
    return _cached(package, body, run_id)


def skip(package: str, body: dict, run_id: str) -> tuple:
    """Flows to leave out: those already tried this case, and those saved for another goal.

    *run_id* is required: without it the goal filter would silently be off (M-01).
    """
    return tried(body) + skipped(package, body, run_id)
