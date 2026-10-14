"""Jira MCP: tool names helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

from tools import settings_port


def tool_prefix() -> str:
    """Client-specific Atlassian MCP tool-name prefix.

    Claude Code / Claude Desktop expose the remote server's tools as
    ``mcp__atlassian__getJiraIssue``; other clients namespace differently, so
    the prefix is configurable (``QA_JIRA_MCP_TOOL_PREFIX``) rather than
    hardcoded into the tester-facing directive. Never raises.
    """
    try:
        value = str(
            getattr(settings_port.get(), "qa_jira_mcp_tool_prefix", "") or ""
        ).strip()
    except (
        AttributeError,
        TypeError,
        ValueError,
    ):  # pragma: no cover - settings is lenient
        value = ""
    return value or "mcp__atlassian__"


# Read-only, takes no parameters, and cannot fail for "wrong project" reasons --
# so a failure here really does mean "not connected".
_VERIFY_TOOL = "atlassianUserInfo"


# The ACCESS half (2026-09-01). atlassianUserInfo proves a SESSION EXISTS and
# says nothing about whether it can reach THIS tester's Jira site. Also
# read-only and parameterless.
_ACCESS_TOOL = "getAccessibleAtlassianResources"


# The per-ticket FETCH tool every Jira boomerang directive names (2026-09-26,
# v1.98 scope B / B1-B2-B4): a shared constant + accessor so a refusal that
# needs to NAME the tool never hand-types a prefix-less default that can
# drift from what build_fetch_directive already sends.
_FETCH_TOOL = "getJiraIssue"


def access_tool_name() -> str:
    """Fully-qualified name of the read-only ACCESS probe the AGENT must call.

    Same client-configurable prefix as :func:`verify_tool_name`, so the default
    is exactly ``mcp__atlassian__getAccessibleAtlassianResources``.
    """
    return f"{tool_prefix()}{_ACCESS_TOOL}"


def fetch_tool_name() -> str:
    """Fully-qualified name of the per-ticket FETCH tool the AGENT must call.

    Same client-configurable prefix as :func:`verify_tool_name`, so the default
    is exactly ``mcp__atlassian__getJiraIssue`` -- used by refusals that must
    NAME the tool the agent already has, not merely assert one exists.
    """
    return f"{tool_prefix()}{_FETCH_TOOL}"


def probe_provenance() -> str:
    """The tool-name string a verdict recorded by TODAY's probe carries.

    Recorded on every persisted verdict and re-checked on every read, so a
    verdict produced by an OLDER probe cannot masquerade as a current one. A
    plain string rather than a version number because that is what the store's
    column already holds, and because someone reading the database should be
    able to see which checks actually ran.
    """
    return f"{verify_tool_name()}+{access_tool_name()}"


def probe_provenance_accepted() -> frozenset:
    """Provenance strings a stored verdict may carry and still count as CURRENT.

    Exactly one entry today. The identity-only string is deliberately NOT in it:
    a verdict recorded before this change proved a session EXISTS, never that it
    could reach the configured Jira site, which is the question this change
    added. Those rows re-ask once and self-heal.
    """
    return frozenset({probe_provenance()})


def verify_tool_name() -> str:
    """Fully-qualified name of the read-only probe tool the AGENT must call.

    Uses the same client-configurable prefix as every other directive, so the
    default is exactly ``mcp__atlassian__atlassianUserInfo``.
    """
    return f"{tool_prefix()}{_VERIFY_TOOL}"
