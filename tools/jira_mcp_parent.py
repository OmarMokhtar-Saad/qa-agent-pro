"""Jira MCP: parent helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import logging

from tools import settings_port
from tools.jira_mcp_ac import _extract_ac_from_description
from tools.jira_mcp_ac_field import usable_ac_text
from tools.jira_mcp_adf import _as_text
from tools.jira_mcp_context import _build_parent_context, _ParentBg
from tools.jira_mcp_keys import _valid_issue_key
from tools.jira_mcp_nodes import _issue_fields
from tools.jira_mcp_related import (
    _extract_issuelinks,
    _extract_parent_ref,
    _extract_subtasks,
)
from tools.jira_mcp_sibling_count import _count_sibling_candidates
from tools.jira_mcp_siblings import _extract_sibling_bodies

logger = logging.getLogger("tools.jira_mcp")


def _own_link_ref(fields: dict, key: str) -> dict | None:
    """This issue's own link entry for *key*, or None (logged) when not linked."""
    linked = {
        item.get("key"): item
        for item in _extract_issuelinks(fields)
        if isinstance(item, dict)
    }
    if key not in linked:
        logger.info(
            "parent_issue %s is not linked from this ticket -- ignoring it.", key
        )
        return None
    return linked[key]


def _parent_ac(ac_raw: str, description: str) -> str:
    """The parent's AC: the AC field when usable, else the description's AC block."""
    if usable_ac_text(ac_raw):
        return ac_raw
    return _extract_ac_from_description(description)


# {description, acceptance_criteria} from an optional second getJiraIssue
# payload for the parent story. {} when absent/unusable. Never raises.
def _parent_body(parent_issue: object) -> dict:
    """{description, acceptance_criteria} for the parent story, or {}. Never raises."""
    try:
        if not settings_port.get().jira_fetch_parent:
            return {}
        fields = _issue_fields(parent_issue)
        if not fields:
            return {}
        description = _as_text(fields.get("description")).strip()
        ac_raw = _as_text(fields.get(settings_port.get().jira_ac_field)).strip()
        return {
            "description": description,
            "acceptance_criteria": _parent_ac(ac_raw, description),
        }
    except Exception:
        logger.exception("Extracting the parent story body failed - omitting it")
        return {}


# Parent-shaped ref for a requirements story reached by an ISSUE LINK.
#
# WHY: ``_extract_parent_ref`` reads ``fields.parent`` only, and the merge of
# the client's second fetch is guarded by ``if parent:``. A ticket whose
# requirements hang off a LINK rather than a hierarchy therefore had the
# submitted ``parent_issue`` -- description, acceptance criteria and all --
# dropped on the floor, silently, while the link's one-line TITLE still
# appeared in the context because ``_extract_issuelinks`` reads it separately.
# The suite was then generated from whatever thin text the task ticket
# carried, and invented the rest.
#
# MEASURED on TICKET-10884 (2026-08-30): a `[BE]` task whose own description is
# three lines pointing at unreadable attachments, linked by a Polaris
# "implements" relation to TICKET-10418, whose description IS the requirement --
# the notification table with the exact Ar/En title, body and button copy.
# None of that table reached the model. An earlier run on the same ticket
# duly asserted 'How was your visit?' and 'Rate Now' as UI strings "the source
# never promises"; the source promised them, on the story next door.
#
# SAFETY: the submitted body is accepted ONLY when its key is already among
# THIS issue's own ``issuelinks``. The client cannot introduce an arbitrary
# ticket as requirements -- the link has to exist in the ticket the tester
# asked about. Everything downstream is unchanged: the text is still routed
# through ``wrap_untrusted`` and still labelled BACKGROUND ONLY.
#
# Returns None when there is no usable link/body pair. Never raises.
def _link_ref_record(key: str, ref: dict, body: dict) -> dict:
    """The parent-shaped record for a linked story: key, link summary/type, body."""
    return {
        "key": key,
        "summary": str(ref.get("summary") or "").strip(),
        "issuetype": str(ref.get("issuetype") or "").strip(),
        **body,
    }


def _linked_parent_ref(fields: dict, parent_issue: object) -> dict | None:
    """Parent-shaped ref for a requirements story reached by an ISSUE LINK."""
    try:
        body = _parent_body(parent_issue)
        if not body or not (body.get("description") or "").strip():
            return None
        raw_key = parent_issue.get("key") if isinstance(parent_issue, dict) else None
        key = _valid_issue_key(raw_key)
        ref = _own_link_ref(fields, key) if key else None
        return _link_ref_record(key, ref, body) if ref is not None else None
    except Exception:
        logger.exception("Resolving a linked requirements story failed - omitting it")
        return None


def _grounding_refs(fields: dict, payload: dict) -> tuple:
    """``(parent, subtasks, issuelinks)`` for a grounded fetch."""
    parent: dict | None = _extract_parent_ref(fields)
    subtasks = _extract_subtasks(fields)
    issuelinks = _extract_issuelinks(fields)
    if parent:
        parent = {**parent, **_parent_body(payload.get("parent_issue"))}
    else:
        # No `fields.parent`, but the client may still have fetched the
        # story this ticket IMPLEMENTS. Accepted only when that key is
        # already linked from here -- see _linked_parent_ref.
        parent = _linked_parent_ref(fields, payload.get("parent_issue"))
    return parent, subtasks, issuelinks


# ``(parent, subtasks, issuelinks, siblings, parent_context)``; all empty
# unless ``settings_port.get().jira_fetch_parent`` is on.
def _parent_grounding(fields: dict, payload: dict, key: str) -> tuple:
    """The five grounding values; all empty unless jira_fetch_parent is on."""
    if not settings_port.get().jira_fetch_parent:
        return None, [], [], [], ""
    parent, subtasks, issuelinks = _grounding_refs(fields, payload)
    siblings = _extract_sibling_bodies(payload, key)
    total = _count_sibling_candidates(payload, key)
    parent_context = _build_parent_context(
        _ParentBg(parent, subtasks, issuelinks, siblings, sibling_total=total)
    )
    return parent, subtasks, issuelinks, siblings, parent_context
