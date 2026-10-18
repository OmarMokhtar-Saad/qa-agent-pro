"""One-time scrub of goals already saved in a package's flows (see ``goal_scrub``).

A goal saved before the scrub existed may still hold a device serial. ``run`` rewrites
every stored ``meta.goal`` through ``goal_scrub.scrub`` once per package and leaves a
marker in the flow file, so later calls cost one read. Never raises.
"""

from __future__ import annotations

import logging

from tools.mobile import app_flows, app_store, goal_scrub

logger = logging.getLogger(__name__)
MARKER = "goals_scrubbed"


def _scrub_entries(body: dict) -> str:
    """Scrub each flow's goal in place, set the marker; ``''`` (nothing refused).

    An empty body is a missing or unreadable file (``read_json`` gives ``{}`` for both):
    refuse, so a corrupt flow file is left as it is, never rewritten as just the marker.
    """
    if not body:
        return "flow file unreadable or empty"
    flows = body.get("flows")
    for entry in (flows if isinstance(flows, dict) else {}).values():
        meta = entry.get("meta") if isinstance(entry, dict) else None
        if isinstance(meta, dict) and meta.get("goal"):
            meta["goal"] = goal_scrub.scrub(str(meta["goal"]))
    body[MARKER] = True
    return ""


def run(package: str) -> bool:
    """True when this call scrubbed the package's saved goals (False: done before/failed)."""
    try:
        if app_store.read_json(package, app_flows.FLOWS_FILE).get(MARKER):
            return False
        done = app_store.update_json(package, app_flows.FLOWS_FILE, _scrub_entries)
        return not done.get("error")
    except Exception:
        logger.exception("flow goal migration failed")
        return False
