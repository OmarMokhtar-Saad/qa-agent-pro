"""Executor plug flows: the app's saved flows go into the run snapshot.

Only ``snapshot`` (no hot-path seam): ``[{name, steps, params, fields}]`` from
``flows.json``, so the first packet can offer "known flow available". File reads
happen once per run, inside ``_read_knowledge``'s worker thread. Never raises.
"""

from __future__ import annotations

import logging

from tools.mobile import app_flows

logger = logging.getLogger(__name__)

PRIORITY = 90


def snapshot(conn, facts) -> dict:
    """The saved flows of the run's app (empty when none or unreadable)."""
    package = str(getattr(facts, "package", "") or "")
    try:
        got = app_flows.list_flows(package) if package else {}
    except Exception:
        logger.exception("knowledge flows snapshot failed")
        return {"flows": []}
    rows = got.get("content") if isinstance(got, dict) else None
    return {"flows": rows if isinstance(rows, list) else []}
