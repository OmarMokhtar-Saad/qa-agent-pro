"""Which saved flows the host lane may auto-replay for ONE case, and whether the user said no.

B17: a flow matches on app + first screen key; its goal text (``meta.goal``) is only
advisory and never drops a flow, so a reworded goal still finds it. What does stop the
host lane is the user's own words refusing saved flows. No app or label is named here.
"""

from __future__ import annotations

import logging

from tools.mobile import flow_goal_fit, flow_replay_state, run_store

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


def skip(package: str, body: dict) -> tuple:
    """Flows to leave out: those already tried this case (B17: goal text never excludes)."""
    return tuple((body.get("auto_replay") or {}).get("done") or ())
