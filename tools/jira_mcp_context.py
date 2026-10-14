"""Jira MCP: context helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import logging
from typing import NamedTuple, cast

from tools import settings_port
from tools.jira_mcp_urls import _strip_urls

logger = logging.getLogger("tools.jira_mcp")


def _context_parent_lines(parent: dict) -> list[str]:
    """The BACKGROUND lines for the parent issue itself."""
    label = parent.get("issuetype") or "issue"
    head = f"Parent {label} {parent['key']}: {parent.get('summary', '')}"
    lines = [head.strip()]
    desc = str(parent.get("description") or "").strip()
    if desc:
        lines += ["", desc]
    pac = str(parent.get("acceptance_criteria") or "").strip()
    if pac:
        lines += ["", "Parent acceptance criteria:", pac]
    return lines


def _context_subtask_lines(subtasks: list[dict], has_parent: bool) -> list[str]:
    """The heading plus one line per sub-task for the BACKGROUND block."""
    heading = (
        "Other sub-tasks under the same parent (context only):"
        if has_parent
        else "Sub-tasks this story is broken down into (context only):"
    )
    lines = ["", heading]
    for sub_task in subtasks:
        status = str(sub_task.get("status") or "").strip()
        suffix = f" [{status}]" if status else ""
        lines.append(
            f"- {sub_task['key']}: {sub_task.get('summary', '')}{suffix}".rstrip()
        )
    return lines


def _sibling_block(sib: dict) -> str:
    """One sibling story rendered as an indented text block."""
    head = f"- {sib['key']}"
    itype = str(sib.get("issuetype") or "").strip()
    if itype:
        head += f" ({itype})"
    sib_summary = str(sib.get("summary") or "").strip()
    if sib_summary:
        head += f": {sib_summary}"
    block = [head.rstrip()]
    sib_desc = str(sib.get("description") or "").strip()
    if sib_desc:
        block.append(f"    {sib_desc}")
    sib_ac = str(sib.get("acceptance_criteria") or "").strip()
    if sib_ac:
        block += ["    Acceptance criteria:", f"    {sib_ac}"]
    return "\n".join(block)


def _context_sibling_lines(siblings: list[dict], sibling_total: int) -> list[str]:
    """The disclosure heading plus the sibling blocks, within the char budget."""
    # A dropped story is disclosed, never silent: the tester has to know
    # the background is a SAMPLE before trusting its coverage.
    shown = len(siblings)
    scope = f" -- showing {shown} of {sibling_total}" if sibling_total > shown else ""
    head = (
        f"Sibling user stories under the same parent{scope} (context only "
        "-- requirements they state can apply to this ticket, but they "
        "are NOT the thing under test):"
    )
    lines = ["", head]
    budget = max(0, settings_port.get().jira_max_sibling_chars)
    for sib in siblings:
        if budget <= 0:
            break
        chunk = _sibling_block(sib)[:budget]
        budget -= len(chunk)
        lines.append(chunk)
    return lines


def _context_links_lines(issuelinks: list[dict]) -> list[str]:
    """The "Linked issues" heading plus one line per link."""
    lines = ["", "Linked issues (context only):"]
    lines += [
        f"- {ln.get('relation', 'relates to')} {ln['key']}: "
        f"{ln.get('summary', '')}".rstrip()
        for ln in issuelinks
    ]
    return lines


def _context_body(
    parent: dict | None, subtasks: list[dict], issuelinks: list[dict]
) -> list[str]:
    """Parent, sub-task and linked-issue lines (the sibling-free part)."""
    has_parent = bool(parent and parent.get("key"))
    lines: list[str] = []
    if has_parent:
        lines += _context_parent_lines(cast("dict", parent))
    if subtasks:
        lines += _context_subtask_lines(subtasks, has_parent)
    if issuelinks:
        lines += _context_links_lines(issuelinks)
    return lines


# The sibling budget is ADDITIVE. Capping the composed block at
# jira_max_parent_chars alone would let sibling prose truncate away the
# parent's own description -- the very thing this block exists to carry.
def _context_cap(siblings: list[dict] | None) -> int:
    """The char cap for the composed block (parent cap plus sibling budget)."""
    cap = settings_port.get().jira_max_parent_chars
    if cap > 0 and siblings:
        cap += max(0, settings_port.get().jira_max_sibling_chars)
    return cap


class _ParentBg(NamedTuple):
    """The inputs of :func:`_build_parent_context` (one parameter object)."""

    parent: dict | None
    subtasks: list[dict]
    issuelinks: list[dict]
    siblings: list[dict] | None = None
    sibling_total: int = 0


# Compose the BACKGROUND block for a ticket whose requirements live elsewhere.
# Plain text only: every http(s) URL is stripped and the result is capped at
# settings_port.get().jira_max_parent_chars. Returned under its OWN key
# (``parent_context``) and deliberately NOT merged into raw_text/description --
# a link inside somebody else's story must never become this ticket's
# navigation target (TICKET-7154). Never raises.
def _build_parent_context(bg: _ParentBg) -> str:
    """The BACKGROUND block for a ticket whose requirements live elsewhere."""
    try:
        lines = _context_body(bg.parent, bg.subtasks, bg.issuelinks)
        if bg.siblings:
            lines += _context_sibling_lines(bg.siblings, bg.sibling_total)
        text = "\n".join(lines).strip()
        cap = _context_cap(bg.siblings)
        if not text or cap <= 0:
            return ""
        return _strip_urls(text)[:cap]
    except Exception:
        logger.exception("Building the Jira parent context failed - omitting it")
        return ""
