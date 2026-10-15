"""Pruning stale flows once a better one is saved (see ``knowledge_stage_flows``).

After a flow that reaches the goal is saved, the package's older flows that stop short
of it (or carry no screen record, as legacy flows do) and are shorter than the new
route only mislead the next run, so they are deleted. Never raises.
"""

from __future__ import annotations

import logging

from tools.mobile import app_flows

logger = logging.getLogger(__name__)


def _stale(row: dict, keep: set, total: int) -> bool:
    return (
        row["name"] not in keep
        and not row.get("reached")
        and int(row.get("steps") or 0) < total
    )


def prune(package: str, keep: list, total: int) -> list:
    """Delete the stale flows of *package*; the names removed.

    *keep* names the packets just saved; *total* is the step count of the whole new route.
    """
    removed: list = []
    try:
        rows = app_flows.list_flows(package).get("content") or []
        for row in rows:
            if _stale(row, set(keep), total):
                if not app_flows.delete_flow(package, row["name"]).get("error"):
                    removed.append(row["name"])
    except Exception:
        logger.exception("flow prune failed")
    return removed
