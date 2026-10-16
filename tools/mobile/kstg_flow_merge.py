"""Merging a re-learned route into the flow already saved for it (see ``knowledge_stage_flows``).

A route with the same goal and action signature gets the same flow name, so saving it
again keeps the stored counters and counts one more learn run (``seen``) instead of
resetting them. Never raises.
"""

from __future__ import annotations

import logging

from tools.mobile import app_flows, flow_stats

logger = logging.getLogger(__name__)


def stored_stats(package: str, name: str) -> dict:
    """The counters of the saved flow *name* (``{}`` when none is saved)."""
    try:
        got = app_flows.get_flow(package, name)
        return {} if got.get("error") else flow_stats.of(got["content"])
    except Exception:
        logger.exception("flow merge: stored stats failed")
        return {}


def merged_meta(package: str, name: str, meta: dict) -> dict:
    """*meta* for a flow about to be saved as *name*, with the counters it earned."""
    old = stored_stats(package, name)
    if not old:
        return dict(meta)
    return dict(meta, stats=flow_stats.bump(old, False, learned=True))
