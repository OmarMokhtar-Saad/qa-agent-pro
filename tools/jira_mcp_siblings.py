"""Jira MCP: siblings helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from itertools import islice

from tools import settings_port
from tools.jira_mcp_ac_field import usable_ac_text
from tools.jira_mcp_adf import _as_text
from tools.jira_mcp_keys import _valid_issue_key
from tools.jira_mcp_nodes import _issue_fields, _issue_nodes
from tools.jira_mcp_tables import _flatten_table_text

logger = logging.getLogger("tools.jira_mcp")


def _sibling_items(payload: dict) -> object:
    """The raw ``sibling_issues`` list, unwrapped from a JQL response if needed."""
    items = payload.get("sibling_issues") if isinstance(payload, dict) else None
    if isinstance(items, dict):
        # Accept the RAW JQL response shape as well as a bare list, so a
        # host that pastes the tool result unmodified works. 2026-08-31
        # (F8): the LIVE server nests one level deeper -- {"issues":
        # {"totalCount": n, "nodes": [...]}} -- so this unwrapped to a DICT,
        # failed the list check and dropped every sibling BODY, while
        # _count_sibling_candidates (which does read totalCount) kept
        # reporting how many siblings existed. The count and the bodies
        # disagreed by construction.
        items = _issue_nodes(items) or items.get("issues")
    return items


# SAME gate as the target ticket's own AC (see usable_ac_text):
# JIRA_AC_FIELD is a per-instance GUESS and is a DATE field on this
# workspace, so a live 2026-08-03 run rendered SIX sibling entries as
# "Acceptance criteria: 2026-05-06T11:27:27.047+0300". Reusing the
# gate keeps this path and rtm's downstream judgement in agreement.
def _sibling_ac(sfields: dict, per_issue: int) -> str:
    """The sibling's capped AC text, or "" when the AC field is not usable."""
    sac_raw = _as_text(sfields.get(settings_port.get().jira_ac_field))
    return _flatten_table_text(sac_raw)[:per_issue] if usable_ac_text(sac_raw) else ""


def _sibling_record(item: dict, key: str, per_issue: int) -> dict | None:
    """One sibling's capped record, or None for a title-only sibling."""
    sfields = _issue_fields(item)
    desc = _flatten_table_text(_as_text(sfields.get("description")))[:per_issue]
    sac = _sibling_ac(sfields, per_issue)
    if not desc and not sac:
        # Title-only siblings add nothing _extract_subtasks does not
        # already list, and each one costs budget.
        return None
    itype = sfields.get("issuetype")
    return {
        "key": key,
        "issuetype": (
            str(itype.get("name") or "").strip() if isinstance(itype, dict) else ""
        ),
        "summary": str(sfields.get("summary") or "").strip(),
        "description": desc,
        "acceptance_criteria": sac,
    }


def _sibling_pool(items: list, target: str | None, per_issue: int) -> Iterator[dict]:
    """Yield each usable sibling record (valid key, not the target), lazily."""
    for item in items:
        if not isinstance(item, dict):
            continue
        key = _valid_issue_key(item.get("key"))
        if not key or key == target:
            continue
        record = _sibling_record(item, key, per_issue)
        if record is not None:
            yield record


# [{key, issuetype, summary, description, acceptance_criteria}] for the user
# stories under the same parent.
#
# Read from an OPTIONAL third host call (``searchJiraIssuesUsingJql``) handed
# back as ``sibling_issues``: those child issues' BODIES carry the
# requirements a sub-task inherits, while ``_extract_subtasks`` only ever sees
# key/summary/status. Gated by ``settings_port.get().jira_fetch_sibling_stories``,
# count-capped by ``_MAX_RELATED_ISSUES`` and char-capped PER ISSUE so one
# huge story cannot crowd out the rest.
#
# UNTRUSTED third-party text -- strictly higher injection risk than the target
# ticket, because it is authored by other people for another purpose. Keys are
# regex-gated here, every URL is stripped by :func:`_build_parent_context`, and
# the result stays in ``parent_context`` (BACKGROUND) instead of ``raw_text``.
# Never raises.
def _extract_sibling_bodies(payload: dict, target_key: str = "") -> list[dict]:
    """The sibling user stories' bodies (capped, never raises)."""
    try:
        if not settings_port.get().jira_fetch_sibling_stories:
            return []
        items = _sibling_items(payload)
        if not isinstance(items, list):
            return []
        target = _valid_issue_key(target_key)
        max_stories = max(0, int(settings_port.get().jira_max_sibling_stories or 0))
        if max_stories <= 0:
            return []
        per_issue = max(1, settings_port.get().jira_max_sibling_chars // max_stories)
        return list(islice(_sibling_pool(items, target, per_issue), max_stories))
    except Exception:
        logger.exception("Extracting sibling user stories failed")
        return []
