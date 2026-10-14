"""The two dicts the first planning checkpoint writes and returns."""

from __future__ import annotations

from tools.mobile.case_run_types import _Opened, _Plan


def _planning_record(opened: _Opened, screen: dict, plan: _Plan) -> dict:
    """The case record stored while the case waits for the planner's script."""
    return {
        "tc_id": opened.tc_id,
        "title": opened.view.get("title") or "",
        "verdict": "",
        "status": "planning",
        "escapes": plan.used,
        "screen_id": screen.get("screen_id") or "",
        "started": plan.started,
        "evidence": opened.evidence,
    }


def _planning_payload(opened: _Opened, screen: dict, plan: _Plan) -> dict:
    """The packet payload ``start_case`` hands back to the caller."""
    return {
        "error": None,
        "content": {
            "tc_id": opened.tc_id,
            "screen": screen,
            "packet": plan.packet,
            "escapes": plan.used,
        },
    }
