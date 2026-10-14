"""Jira MCP: nodes helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise

logger = logging.getLogger("tools.jira_mcp")


def _extract_priority(fields: dict) -> str:
    """Priority is a {name: "High", ...} object, or absent on some issue types."""
    priority = fields.get("priority") if isinstance(fields, dict) else None
    if isinstance(priority, dict):
        return priority.get("name", "") or ""
    return ""


def _extract_names(items: object) -> list[str]:
    """Extract display names from a Jira field that's a list of either plain
    strings (labels) or {name: ...} objects (components). Never raises."""
    if not isinstance(items, list):
        return []
    names: list[str] = []
    for item in items:
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
        elif isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
    return names


# Issue objects inside the LIVE Atlassian MCP envelope, or [].
#
# 2026-08-31 (F8). ``getJiraIssue`` and ``searchJiraIssuesUsingJql`` both
# return ``{"issues": {"totalCount": n, "nodes": [ {key, fields} ]}}``, while
# the fetch DIRECTIVE asks the host to pass that result RAW and unmodified.
# Nothing unwrapped it, so obeying the directive was refused with "that Jira
# payload has no `fields` object -- call getJiraIssue again and pass its RAW
# result", which loops. Accepts the older ``{"issues": [...]}`` shape too.
# Never raises.
def _issue_nodes(payload: object) -> list:
    """Issue objects inside the LIVE Atlassian MCP envelope, or []."""
    with never_raise():
        if not isinstance(payload, dict):
            return []
        container = payload.get("issues")
        if isinstance(container, dict):
            container = container.get("nodes")
        if not isinstance(container, list):
            return []
        return [item for item in container if isinstance(item, dict)]
    return []


# The issue object the CONTENT will be read from, unwrapping the live
# Atlassian MCP envelope. ``{}`` for a non-dict. Never raises.
#
# 2026-09-02 audit F5, round 2. This used to be three lines inside
# :func:`_issue_fields`, which meant the fields came from ``issues[0]`` while
# :func:`normalize_issue_payload` read ``key`` from the OUTER dict -- so an
# envelope payload for the wrong ticket had ``payload_key == ""`` and slid
# straight past the mismatch guard. Any check on the identity of a payload
# MUST read the same node the content comes from, so both callers now share
# this one selector.
def _issue_node(issue: object) -> dict:
    """The issue object the CONTENT will be read from."""
    with never_raise():
        if not isinstance(issue, dict):
            return {}
        if "fields" not in issue:
            nodes = _issue_nodes(issue)
            if nodes:
                return nodes[0]
        return issue
    return {}


def _issue_fields(issue: object) -> dict:
    """The ``fields`` mapping of an issue payload (or {} ). Never raises."""
    with never_raise():
        if not isinstance(issue, dict):
            return {}
        # F8: unwrap the live envelope before looking for `fields`.
        issue = _issue_node(issue) or issue
        fields = issue.get("fields")
        if isinstance(fields, dict):
            return fields
        # Some MCP responses flatten the issue (summary/description at top
        # level). Accept that shape too rather than silently returning nothing.
        if any(k in issue for k in ("summary", "description")):
            return issue
        return {}
    return {}


def _name_of(value: object) -> str:
    """``{"name": "Story"}`` -> ``"Story"``; a bare string passes through.

    Atlassian returns issue type, status and priority as name objects. Never
    raises -- an unexpected shape yields "" and the caller omits the line.
    """
    # defensive
    with never_raise(logger, "_name_of failed", exc_info=True):
        if isinstance(value, dict):
            return str(value.get("name") or "").strip()
        if isinstance(value, str):
            return value.strip()
    return ""
