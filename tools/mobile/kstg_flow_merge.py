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


def with_replaced(package: str, replaces: list, meta: dict) -> dict:
    """*meta* plus the counters of the flows *replaces* its flow takes over.

    A replaced flow is deleted, so the replays it earned (``runs``, ``ok``) move to the
    flow that replaces it: the stats stay on the flow that is still saved.
    """
    got = [stored_stats(package, name) for name in replaces]
    stats = flow_stats.clean(meta.get("stats")) or {}
    for one in got:
        for key in ("runs", "ok", "seen"):
            stats[key] = stats.get(key, 0) + one.get(key, 0)
        stats["last"] = max(stats.get("last", 0.0), one.get("last", 0.0))
    stats = flow_stats.clean(stats)
    return dict(meta, stats=stats) if stats else meta


def merged_meta(package: str, name: str, meta: dict) -> dict:
    """*meta* for a flow about to be saved as *name*, with the counters it earned."""
    old = stored_stats(package, name)
    if not old:
        return dict(meta)
    return dict(meta, stats=flow_stats.bump(old, False, learned=True))
