"""Jira MCP: sibling count helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import logging

from tools.jira_mcp_keys import _valid_issue_key
from tools.jira_mcp_nodes import _issue_nodes

logger = logging.getLogger("tools.jira_mcp")


def _total_hint(container: dict) -> int:
    """The largest ``totalCount`` / ``total`` the container or its ``issues`` reports."""
    # The live Atlassian MCP server returns `totalCount`; REST v3 and
    # older tool versions return `total`. Accept either rather than
    # silently falling back to "however many you sent me". 2026-08-31
    # (F8): on the live shape both the count and the nodes sit INSIDE
    # `issues`, so read that level too -- otherwise the disclosure fell
    # back to counting a list it had failed to find.
    total_hint = 0
    _levels = [container]
    _inner = container.get("issues")
    if isinstance(_inner, dict):
        _levels.append(_inner)
    for _level in _levels:
        for field in ("totalCount", "total"):
            raw_total = _level.get(field)
            if isinstance(raw_total, int) and not isinstance(raw_total, bool):
                total_hint = max(total_hint, raw_total)
    return total_hint


def _candidate_lists(container: object) -> tuple:
    """``(items, total_hint)`` read from the ``sibling_issues`` container."""
    if not isinstance(container, dict):
        return container, 0
    items = _issue_nodes(container) or container.get("issues")
    return items, _total_hint(container)


def _count_in_list(items: list, target_key: str, total_hint: int) -> int:
    """The sibling count from the delivered *items*, never below the hint."""
    target = _valid_issue_key(target_key)
    sent = sum(
        1
        for i in items
        if isinstance(i, dict)
        and _valid_issue_key(i.get("key"))
        and _valid_issue_key(i.get("key")) != target
    )
    # `total` counts the target issue too when it is a child of the same
    # parent, so never let the hint fall BELOW what we actually counted.
    return max(sent, total_hint - 1 if total_hint > sent else sent)


# How many sibling stories EXIST, for the "showing N of M" disclosure.
#
# Prefers the JQL response's own ``total`` (present when the host passed the
# whole result object, which the directive asks for with
# ``searchResultMode="all"``) and falls back to counting what was sent. That
# distinction matters: the directive caps ``maxResults`` at the number we can
# actually use, so counting only the delivered issues would report "5 of 5"
# for an epic holding 30 stories -- a silent cap dressed as a disclosure.
# Never raises.
def _count_sibling_candidates(payload: dict, target_key: str = "") -> int:
    """How many sibling stories EXIST, for the "showing N of M" disclosure."""
    try:
        container = payload.get("sibling_issues") if isinstance(payload, dict) else None
        items, total_hint = _candidate_lists(container)
        if not isinstance(items, list):
            return total_hint
        return _count_in_list(items, target_key, total_hint)
    except Exception:
        logger.exception("Counting sibling candidates failed")
        return 0
