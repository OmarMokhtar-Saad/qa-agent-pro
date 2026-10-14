"""Jira MCP: related helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import logging

from tools.jira_mcp_keys import _valid_issue_key

logger = logging.getLogger("tools.jira_mcp")

# Cap on how many sibling sub-tasks / linked issues are listed as context.
_MAX_RELATED_ISSUES = 10


def _fields_dict(node: dict) -> dict:
    """node["fields"] when it is a dict, else {}."""
    value = node.get("fields")
    return value if isinstance(value, dict) else {}


def _obj_name(value: object) -> str:
    """The stripped "name" of a Jira named object, or "" when it is not one."""
    return str(value.get("name") or "").strip() if isinstance(value, dict) else ""


def _extract_parent_ref(fields: dict) -> dict | None:
    """{key, summary, issuetype} for this issue's parent, or None. Never raises."""
    try:
        parent = fields.get("parent") if isinstance(fields, dict) else None
        if not isinstance(parent, dict):
            return None
        key = _valid_issue_key(parent.get("key"))
        if not key:
            return None
        pfields = _fields_dict(parent)
        return {
            "key": key,
            "summary": str(pfields.get("summary") or "").strip(),
            "issuetype": _obj_name(pfields.get("issuetype")),
        }
    except Exception:
        logger.exception("Extracting the Jira parent reference failed")
        return None


def _subtask_record(item: object) -> dict | None:
    """{key, summary, status} for one sub-task entry, or None when unusable."""
    if not isinstance(item, dict):
        return None
    key = _valid_issue_key(item.get("key"))
    if not key:
        return None
    sfields = _fields_dict(item)
    return {
        "key": key,
        "summary": str(sfields.get("summary") or "").strip(),
        "status": _obj_name(sfields.get("status")),
    }


def _extract_subtasks(fields: dict) -> list[dict]:
    """[{key, summary, status}] for this issue's sub-tasks (capped). Never raises."""
    try:
        items = fields.get("subtasks") if isinstance(fields, dict) else None
        if not isinstance(items, list):
            return []
        out: list[dict] = []
        for item in items:
            record = _subtask_record(item)
            if record is None:
                continue
            out.append(record)
            if len(out) >= _MAX_RELATED_ISSUES:
                break
        return out
    except Exception:
        logger.exception("Extracting Jira sub-tasks failed")
        return []


def _link_record(item: dict) -> dict | None:
    """{relation, key, summary} for the first usable side of one issue link, or None."""
    ltype = item.get("type")
    ltype = ltype if isinstance(ltype, dict) else {}
    for side, phrase_key in (("outwardIssue", "outward"), ("inwardIssue", "inward")):
        issue = item.get(side)
        if not isinstance(issue, dict):
            continue
        key = _valid_issue_key(issue.get("key"))
        if not key:
            continue
        lfields = _fields_dict(issue)
        return {
            "relation": str(ltype.get(phrase_key) or "relates to").strip(),
            "key": key,
            "summary": str(lfields.get("summary") or "").strip(),
        }
    return None


def _extract_issuelinks(fields: dict) -> list[dict]:
    """[{relation, key, summary}] for linked issues (capped). Never raises."""
    try:
        items = fields.get("issuelinks") if isinstance(fields, dict) else None
        if not isinstance(items, list):
            return []
        out: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            record = _link_record(item)
            if record is not None:
                out.append(record)
            if len(out) >= _MAX_RELATED_ISSUES:
                break
        return out
    except Exception:
        logger.exception("Extracting Jira issue links failed")
        return []
