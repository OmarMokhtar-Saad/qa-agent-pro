"""Attach a ``finding`` to a decoded turn, on the explore lane only.

A finding is the explore lane's per-turn note (``actions.TURN_FIELD_SCHEMA``).
The suite lane has no such field, so a finding offered there is left out and the
caller gets a short refusal naming the lane instead of a silent drop.

The text goes through ``actions.script_finding``, the one producer of that
fold-and-cap, so this is not a second rule. The caller's dict is never mutated.
"""

from __future__ import annotations

from tools.mobile import actions, session


def merge_finding(
    payload: dict, finding: object, *, lane: str
) -> tuple[dict, str | None]:
    """A copy of ``payload`` plus the finding, and a refusal text or None.

    Explore lane: the cleaned finding is set when there is one. Any other lane:
    the payload comes back unchanged and, when a finding was given, the refusal
    names the lane. An empty or non-text finding adds nothing and refuses nothing.
    """
    merged = dict(payload) if isinstance(payload, dict) else {}
    text = actions.script_finding({"finding": finding})
    if lane != session.LANE_EXPLORE:
        if not text:
            return merged, None
        shown = repr(str(lane)[:20])
        return merged, (
            "A finding is kept only on an explore turn; this run is on the "
            + shown
            + " lane, so it was left out."
        )
    if text:
        merged["finding"] = text
    return merged, None
