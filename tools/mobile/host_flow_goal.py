"""Which saved flows the host lane may auto-replay for ONE case, and whether the user said no.

A flow learned from a run carries the goal it reached (``meta.goal``); the host lane
saves a passed single case under its title. Such a flow replays only for a case whose
title holds every word stem of that goal, so "wrong password shows an error" never runs
for a valid login that happens to start on the same screen. A flow with no recorded
goal (one saved by hand) is not held back. No app or label is named here.
"""

from __future__ import annotations

import logging

from tools.mobile import flow_goal_fit, flow_replay_match, flow_replay_state, run_store

logger = logging.getLogger(__name__)
_FULL = 5  # ``flow_goal_fit.fit`` returns 0..5; 5 = the case holds every stem


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


def mismatched(package: str, goal: str) -> tuple:
    """Names of saved flows of *package* that reached another goal than *goal*."""
    out = []
    for name, template in flow_replay_match._templates(package):
        wanted = (template.get("meta") or {}).get("goal")
        if wanted and flow_goal_fit.fit(goal, wanted) < _FULL:
            out.append(name)
    return tuple(out)


def skip(package: str, body: dict) -> tuple:
    """Flows to leave out: those already tried plus those for another goal."""
    done = (body.get("auto_replay") or {}).get("done") or ()
    return tuple(done) + mismatched(package, str(body.get("goal") or ""))
