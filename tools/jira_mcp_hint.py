"""Jira MCP: hint helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

from pathlib import Path

import llm
from config.settings_guard import never_raise
from tools import jira_mcp_local_config as _lc
from tools.jira_mcp_connect import _CONNECT_STEPS_BY_CLIENT, _detect_client_key


def _hint_client_key() -> str:
    """The detected client key, or "" when the host lookup fails."""
    with never_raise():
        return _detect_client_key(llm.get_host_client())
    return ""


def _entry_on_disk(key: str, workspace_roots: list[Path] | None) -> bool:
    """Whether an `atlassian` entry is found on disk; a failed lookup is False."""
    with never_raise():
        return _lc.local_atlassian_entry_exists(key, workspace_roots=workspace_roots)
    return False


def _already_configured_hint(key: str) -> str:
    """The sentence for a client whose `atlassian` entry is already on disk."""
    return (
        "An `atlassian` MCP entry is already configured on disk for "
        f"{key.replace('-', ' ').title()}. I can't tell from here whether "
        "it's actually authorized - if pasting a Jira ticket URL doesn't "
        "work, restart your editor (OAuth sessions can also expire and "
        "need re-authenticating)."
    )


def _client_detail(key: str) -> str:
    """The per-client connect detail, without its leading label."""
    detail = _CONNECT_STEPS_BY_CLIENT[key].strip()
    return detail.split(" - ", 1)[1] if " - " in detail else detail


_HINT_BASE = "To paste Jira ticket URLs, connect the Atlassian MCP server"


def _keyed_hint(
    key: str, workspace_roots: list[Path] | None, verify_offered: bool
) -> str:
    """The hint for a detected client key."""
    if _entry_on_disk(key, workspace_roots):
        if verify_offered:
            # 2026-08-31: the caller already emits a "Fix now" item that
            # tells the agent how to SETTLE this in one read-only call.
            # Repeating "I can't tell from here" beside it makes the
            # report answer and shrug at the same question, and buries
            # the actionable item under a redundant optional one.
            return ""
        return _already_configured_hint(key)
    return f"{_HINT_BASE} - {_client_detail(key)}"


# One-line, client-aware Jira-connect hint for compact reports (e.g.
# qa-doctor's optional-items list).
#
# ``workspace_roots`` are the tester's OPEN workspace folder(s), resolved from
# the MCP ``roots`` capability by ``mcp_server.qa-doctor`` and passed
# straight through to the on-disk lookup -- the only authoritative way to find
# the project-scoped config a tester would actually edit. ``None`` (a client
# without ``roots`` support, or a failed lookup) degrades to the
# install-dir / cwd / global candidate chain exactly as before.
#
# Detects the connected MCP client
# the same way connect_steps() does and reuses the SAME per-client detail
# text (exact JSON/URL/restart step) as one inline sentence instead of a
# bulleted block, so a tester gets full actionable steps without first
# having to paste a Jira URL to trigger connect_steps(). When an
# 'atlassian' entry is already found on disk, says so instead of telling
# the tester to add something that is already there -- but never claims
# it is actually connected, since that cannot be observed from here.
# Falls back to a short all-clients summary when the host is
# empty/unrecognized. Never raises.
#
def connect_hint_line(
    workspace_roots: list[Path] | None = None, *, verify_offered: bool = False
) -> str:
    """One-line, client-aware Jira-connect hint for compact reports (e.g."""
    key = _hint_client_key()
    if key:
        return _keyed_hint(key, workspace_roots, verify_offered)
    return (
        f"{_HINT_BASE} in your editor (Claude Code: `/mcp`; Claude Desktop: Settings > "
        "Connectors; Cursor: edit `.cursor/mcp.json`; Gemini CLI: `gemini mcp add`). "
        "No API token and no .env entry are needed."
    )
