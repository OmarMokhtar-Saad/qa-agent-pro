"""Jira MCP: text block of normalize_issue_payload (split from tools/jira_mcp.py)."""

from __future__ import annotations

from typing import NamedTuple

from tools import settings_port
from tools.jira_mcp_ac import _extract_ac_from_description
from tools.jira_mcp_ac_field import resolve_ac_field
from tools.jira_mcp_blocks import SourceText
from tools.jira_mcp_comments import _comment_lines, _extract_comment_records
from tools.jira_mcp_errors import _sanitize_echo
from tools.jira_mcp_nodes import _extract_names, _extract_priority, _name_of


class _Tags(NamedTuple):
    """Priority, labels and components of the issue."""

    priority: str
    labels: list
    components: list


# Never silent: the generator and the tester both have to know that part of
# the ticket did not reach them, or thin coverage reads as a thin ticket.
_TRUNCATED_LINE = (
    "Ticket text: PARTIALLY UNREAD -- part of this ticket is nested "
    "deeper than I parse, so some of it is missing below. Treat gaps "
    "as unknown, not as absent, and ask for the missing section."
)


def _status_line(status_name: str) -> str:
    """The ``Status:`` line with its workflow-state caveat."""
    return (
        f"Status: {status_name} "
        "(workflow state at the time this snapshot was taken -- a ticket "
        "not yet in a done/approved state may still change)"
    )


# F14 (2026-08-30): the fetch DIRECTIVE asks the host for `issuetype` and
# `status` by name, and neither reached the model -- 79 cases were
# written against a ticket still in "BA Validation" without the generator
# ever being told it was not a settled requirement. Both are echoed with
# the same sanitiser `updated` uses (one line, URL-free, backtick-free,
# capped), so a host that trimmed `fields` simply contributes nothing.
def _meta_head(fields: dict, any_truncated: bool) -> list:
    """The truncation disclosure, issue type and status lines."""
    issuetype_name = _sanitize_echo(_name_of(fields.get("issuetype")), 40)
    status_name = _sanitize_echo(_name_of(fields.get("status")), 40)
    lines = []
    if any_truncated:
        lines.append(_TRUNCATED_LINE)
    if issuetype_name:
        lines.append(f"Issue type: {issuetype_name}")
    if status_name:
        lines.append(_status_line(status_name))
    return lines


def _meta_tail(tags: _Tags) -> list:
    """The priority, labels and components lines."""
    lines = []
    if tags.priority:
        lines.append(f"Priority: {tags.priority}")
    if tags.labels:
        lines.append(f"Labels: {', '.join(tags.labels)}")
    if tags.components:
        lines.append(f"Components: {', '.join(tags.components)}")
    return lines


def _compose_meta_block(fields: dict, any_truncated: bool, tags: _Tags) -> str:
    """The ``Issue type / Status / Priority / ...`` lines that open ``raw_text``."""
    meta_lines = _meta_head(fields, any_truncated) + _meta_tail(tags)
    return ("\n".join(meta_lines) + "\n") if meta_lines else ""


# Title, meta block, description and the ``## Comments`` dump.
#
# The raw "## Comments" dump, unchanged from the REST implementation and now
# UNCONDITIONAL. It was briefly conditional: while tools/comment_reconciler was
# enabled the thread was SUPPRESSED here deliberately, so its fenced,
# deterministically-resolved, URL-stripped AMENDMENTS block could be the only
# comment-derived input the generator saw. That seam became a False constant on
# 2026-08-14 and the module was DELETED on 2026-08-15 (dead-code deletion batch
# D5), so the dump is restored permanently -- which is what every install has
# actually done since the pin. A revival must re-suppress it here; see
# docs/RETIRED_CAPABILITIES.md section 4.
def _compose_raw_text(head: str, description: str, comments: list) -> str:
    """Title and meta block, description and the ``## Comments`` dump."""
    raw_text = f"{head}{description}".strip()
    if comments:
        raw_text += "\n\n## Comments\n" + "\n".join(f"- {c}" for c in comments)
    return raw_text


def _tags_of(fields: dict) -> _Tags:
    """Priority, labels and components read from ``fields``."""
    return _Tags(
        _extract_priority(fields),
        _extract_names(fields.get("labels")),
        _extract_names(fields.get("components")),
    )


def _comment_parts(fields: dict) -> tuple[list, list]:
    """``(comment_records, newest-first comment lines)``."""
    records = _extract_comment_records(fields)
    return records, _comment_lines(records)[-settings_port.get().jira_max_comments :][
        ::-1
    ]


# resolve_ac_field re-checks usability and, when enabled, looks for a custom
# field whose value actually reads like requirements.
def _acceptance(fields: dict, description: str) -> str:
    """The acceptance criteria: the AC field, else the description's AC section."""
    return resolve_ac_field(fields)[1] or _extract_ac_from_description(description)


def _text_block(fields: dict, title: str, src: SourceText) -> dict:
    """Title, body text, priority, labels, comments and acceptance criteria."""
    tags = _tags_of(fields)
    records, comments = _comment_parts(fields)
    meta = _compose_meta_block(fields, src.any_truncated, tags)
    return {
        "title": title,
        "description": src.description,
        "acceptance_criteria": _acceptance(fields, src.description),
        "priority": tags.priority,
        "labels": tags.labels,
        "components": tags.components,
        "comments": comments,
        "comments_meta": records,
        "raw_text": _compose_raw_text(f"{title}\n{meta}", src.description, comments),
    }
