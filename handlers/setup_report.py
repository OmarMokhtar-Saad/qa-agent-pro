"""The qa-doctor report head, preps/AC section and action items (moved from setup_check)."""

from __future__ import annotations

import logging

from handlers import host_check, prep_status, setup_state

logger = logging.getLogger("tools.mcp_handlers")


def _head_title_lines(head: setup_state._SetupHead) -> list[str]:
    return [
        "## Setup check",
        "",
        f"**Overall:** {head.verdict}",
        "",
        *([f"**App version:** v{head.app_version}", ""] if head.app_version else []),
        *([head.restart_note, ""] if head.restart_note else []),
        *([head.reload_note, ""] if head.reload_note else []),
        *head.heal_lines,
        *head.atlassian_lines,
    ]


def _head_environment_lines(head: setup_state._SetupHead) -> list[str]:
    return [
        "### Environment",
        f"- {'✅' if head.py_ok else '❌'} **Python** {head.py_version}"
        + ("" if head.py_ok else " — 3.10 or newer required"),
        *([head.export_line] if head.export_line else []),
    ]


def _head_integration_lines(head: setup_state._SetupHead) -> list[str]:
    return [
        "",
        *(
            ["### Integrations", "- " + head.jira_status_line, ""]
            if head.jira_used
            else []
        ),
    ]


def _setup_report_head(head: setup_state._SetupHead) -> list[str]:
    return [
        *_head_title_lines(head),
        *_head_environment_lines(head),
        *_head_integration_lines(head),
        *host_check.tooling_lines(head.tool_paths),
        "",
        "### Feature gates",
    ]


# Item 2b: unfinished host-mode preps (disclosure only, flag-gated;
# empty string when QA_PREP_DISCLOSE_UNFINISHED is off or none exist).
async def _unfinished_and_ac_lines() -> list[str]:
    _unfinished = await prep_status.prep_unfinished_note()
    lines: list[str] = []
    if _unfinished:
        lines += ["", "### Unfinished host-mode preps", _unfinished.rstrip()]
    lines += ["", *host_check.ac_field_section()]
    return lines


def _action_item_lines(
    blockers: list[str], recommended: list[str], optional: list[str]
) -> list[str]:
    items = (
        [("Fix now", item) for item in blockers]
        + [("Recommended", item) for item in recommended]
        + [("Optional", item) for item in optional]
    )
    lines: list[str] = []
    if items:
        lines += ["", "### Action items"]
        for idx, (tag, text) in enumerate(items, 1):
            lines.append(f"{idx}. **{tag}:** {text}")
    return lines
