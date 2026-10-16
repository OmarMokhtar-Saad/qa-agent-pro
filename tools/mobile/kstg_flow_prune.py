"""Pruning weak flows once a better one is saved (see ``knowledge_stage_flows``).

After a flow that reaches the goal is saved, the package's older flows that stop short
of it (or carry no screen record, as legacy flows do) and are shorter than the new
route only mislead the next run, so they are deleted. So are flows that replay badly,
have gone stale, or are dominated by a better duplicate (``kstg_flow_rules``).
Never raises.
"""

from __future__ import annotations

import logging

from tools.mobile import app_flows, kstg_flow_rules

logger = logging.getLogger(__name__)


def _stale(row: dict, keep: set, total: int) -> bool:
    return (
        row["name"] not in keep
        and not row.get("reached")
        and int(row.get("steps") or 0) < total
    )


def _templates(package: str, rows: list) -> list:
    got = [(r["name"], app_flows.get_flow(package, r["name"])) for r in rows]
    return [(n, g["content"]) for n, g in got if not g.get("error")]


def _doomed(package: str, rows: list, kept: tuple) -> list:
    """Names to delete; *kept* is ``(names just saved, steps of the new route)``."""
    keep, total = kept
    short = [r["name"] for r in rows if _stale(r, keep, total)]
    weak = kstg_flow_rules.doomed(_templates(package, rows))
    return list(dict.fromkeys(short + [n for n in weak if n not in keep]))


def prune(package: str, keep: list, total: int) -> list:
    """Delete the weak flows of *package*; the names removed.

    *keep* names the packets just saved; *total* is the step count of the whole new route.
    """
    removed: list = []
    try:
        rows = app_flows.list_flows(package).get("content") or []
        for name in _doomed(package, rows, (set(keep), total)):
            if not app_flows.delete_flow(package, name).get("error"):
                removed.append(name)
    except Exception:
        logger.exception("flow prune failed")
    return removed
