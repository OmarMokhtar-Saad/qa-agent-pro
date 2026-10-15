"""Mobile session explore lane: planned case and manifest write (split from session_explore)."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from tools.mobile import run_store
from tools.mobile.session_explore_ids import (
    EXPLORE_MODULE,
)
from tools.mobile.session_explore_title import explore_turn_title

logger = logging.getLogger("tools.mobile.session")


def _clean_goal(body: dict) -> str:
    """The turn's goal as one clipped line, or a "not recorded" marker."""
    return " ".join(str(body.get("goal") or "").split())[:400] or "not recorded"


def _planned_step(goal: str, finding: object) -> dict:
    """The single step of a planned exploratory case."""
    note = " ".join(str(finding or "").split())[:400]
    return {
        "step_number": 1,
        "action": "Explore towards the goal: " + goal,
        "expected_result": (
            "Recorded finding: " + note
            if note
            else (
                "An exploratory turn has no expected result; it records what was found."
            )
        ),
    }


# Valid rather than ad-hoc because :func:`load_case` and
# :func:`load_run_cases` rehydrate every entry of that list through
# ``TestCase(**body)``: a shape that failed validation would be dropped with
# a warning, and the re-run-failures source would silently lose the run. It
# is also what lets the report's ``_planned_case`` print what the turn was
# FOR.
def _explore_planned_case(tc_id: str, state: object, finding: str = "") -> dict:
    """The turn as a MINIMAL valid ``TestCase`` dump, for ``manifest["cases"]``."""
    body = state if isinstance(state, dict) else {}
    goal = _clean_goal(body)
    return {
        "tc_id": tc_id,
        "module": EXPLORE_MODULE,
        "title": explore_turn_title(body, goal),
        "priority": "Medium",
        "type": "Exploratory",
        "preconditions": "The app was open at the previous turn's screen.",
        "steps": [_planned_step(goal, finding)],
        "automation_status": "Manual",
    }


def _with_planned(manifest: dict, planned: dict) -> None:
    """Replace the planned case of the same ``tc_id`` in the manifest, in place."""
    cases = [
        body
        for body in list(manifest.get("cases") or [])
        if isinstance(body, dict)
        and str(body.get("tc_id") or "") != str(planned["tc_id"])
    ]
    cases.append(planned)
    manifest["cases"] = cases
    manifest["total"] = len(cases)


# *planned* is one ``TestCase``-shaped dict for the turn just submitted. It
# goes in the SAME read-modify-write as the state: this function writes the
# WHOLE manifest, so two separate writes would have the second clobber the
# first.
def _persist_explore(run_id: str, state: object, planned: object = None) -> None:
    """Write the exploratory state back into the manifest, in place."""
    with never_raise(
        logger, "mobile.session: could not persist explore state", level=logging.WARNING
    ):
        manifest = (run_store.read_manifest(run_id) or {}).get("content") or {}
        if not isinstance(manifest, dict):
            return
        manifest["explore"] = state if isinstance(state, dict) else {}
        if isinstance(planned, dict) and planned.get("tc_id"):
            _with_planned(manifest, planned)
        run_store.write_manifest(run_id, manifest)
