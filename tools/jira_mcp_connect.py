"""Jira MCP: connect helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

from urllib.parse import urlparse

import llm
from config.settings_guard import never_raise

_CONNECT_PREAMBLE = "**How to connect Atlassian (one time, ~1 minute):**\n"


_CONNECT_STEP_CLAUDE_CODE = (
    "- **Claude Code** - make sure `.mcp.json` has the `atlassian` entry "
    '(`{"type": "http", "url": "https://mcp.atlassian.com/v1/mcp/authv2"}`), '
    "then run `/mcp` and authenticate `atlassian` in the browser window it opens.\n"
)


_CONNECT_STEP_CLAUDE_DESKTOP = (
    "- **Claude Desktop** - claude.ai -> **Settings -> Connectors -> Atlassian -> "
    "Connect**, then reopen the desktop app.\n"
)


_CONNECT_STEP_CURSOR = (
    "- **Cursor** - Cursor's settings menu path for this moves between versions, so "
    "edit the config file directly: add to `.cursor/mcp.json` (project) or "
    "`~/.cursor/mcp.json` (global) under `mcpServers`: "
    '`"atlassian": {"type": "http", "url": "https://mcp.atlassian.com/v1/mcp/authv2"}`, '
    "then restart Cursor - it completes OAuth automatically on the first "
    "401/`WWW-Authenticate` challenge. (Newer versions also expose this under "
    "**Settings -> Tools & MCP** or the **Customize** page.)\n"
)


_CONNECT_STEP_GEMINI_CLI = (
    "- **Gemini CLI** - `gemini mcp add --transport http atlassian "
    "https://mcp.atlassian.com/v1/mcp/authv2`, then re-run and approve the browser "
    "consent screen.\n"
)


_CONNECT_CLOSING = (
    "\nJira **Cloud** only. Once `atlassian` shows as connected, paste the ticket URL "
    "again and I'll read it through your own connection - no API token, and nothing "
    "is stored on this machine. Never search for or read API tokens, keychains, "
    "credential files, or another workspace to connect instead -- only the browser "
    "consent screen above authorizes it."
)


_CONNECT_STEPS_BY_CLIENT = {
    "claude-code": _CONNECT_STEP_CLAUDE_CODE,
    "claude-desktop": _CONNECT_STEP_CLAUDE_DESKTOP,
    "cursor": _CONNECT_STEP_CURSOR,
    "gemini-cli": _CONNECT_STEP_GEMINI_CLI,
}


_CONNECT_STEPS = (
    _CONNECT_PREAMBLE
    + _CONNECT_STEP_CLAUDE_CODE
    + _CONNECT_STEP_CLAUDE_DESKTOP
    + _CONNECT_STEP_CURSOR
    + _CONNECT_STEP_GEMINI_CLI
    + _CONNECT_CLOSING
)


def _detect_client_key(host: str) -> str:
    """Best-effort match of an MCP clientInfo.name to one of the 4 documented
    clients. Returns '' when the host is empty/unrecognized (caller should
    fall back to showing all four)."""
    h = (host or "").strip().lower()
    if "cursor" in h:
        return "cursor"
    if "gemini" in h:
        return "gemini-cli"
    if "desktop" in h:
        return "claude-desktop"
    if "claude" in h:
        return "claude-code"
    return ""


def connect_steps() -> str:
    """Per-client Atlassian MCP connection instructions, with no error framing.

    Detects the connected MCP client (forwarded from the initialize handshake
    via llm.set_host_client) and returns ONLY that client's steps when
    recognized, so testers aren't shown instructions for editors they don't
    use. Falls back to all four clients when the host is empty/unrecognized.
    Never raises.
    """
    with never_raise() as g:
        key = _detect_client_key(llm.get_host_client())
    if g.failed:
        key = ""
    if key:
        return _CONNECT_PREAMBLE + _CONNECT_STEPS_BY_CLIENT[key] + _CONNECT_CLOSING
    return _CONNECT_STEPS


def not_connected_message(host: str = "") -> str:
    """Actionable message for a tester whose agent has no Atlassian MCP
    connection. Never raises, never blames the tester, never a dead end.

    2026-08-31: this used to splice in ``_CONNECT_STEPS`` -- all four editors --
    while :func:`connect_steps` narrowed to the ONE client the initialize
    handshake named. A live Cursor session proved the split: ``qa_configure_jira``
    showed Cursor only, and the very same session's fetch directive showed Claude
    Code, Claude Desktop, Cursor and Gemini CLI. One narrowing helper now serves
    both, so a tester is never handed steps for editors they do not run.
    """
    where = f" (`{host}`)" if host else ""
    return (
        f"⚠️ **I can't read that Jira ticket{where} yet.**\n\n"
        "Jira access now runs through **your own Atlassian MCP connection** "
        "(OAuth, in your editor) instead of an API token stored on this machine. "
        "I could not find a connected `atlassian` MCP server in this session.\n\n"
        + connect_steps()
    )


# Refusal for a URL on a Jira host that names no ISSUE. Never raises.
#
# Board, backlog, dashboard and Confluence URLs carry no issue key. They used
# to fall through to the generic fetcher, which scraped the anonymous Jira SPA
# shell and handed its ``<title>`` -- the single word "Jira" -- back as the
# whole feature description; eight categories of test cases were then generated
# from that one word with no disclosure at all. Refusing by name is the
# behaviour :func:`tools.jira_fetcher.fetch_url_content` already documented.
def jira_page_without_issue_message(url: str) -> str:
    """Refusal for a URL on a Jira host that names no ISSUE. Never raises."""
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        host = ""
    where = f" (`{host}`)" if host else ""
    return (
        f"⚠️ **That's a Jira page{where}, but not a Jira TICKET.**\n\n"
        "Board, backlog, dashboard and Confluence URLs carry no issue key, and an "
        "anonymous Jira page is an empty JavaScript shell -- reading it would give "
        "me its page title and nothing else, so any suite I generated from it "
        "would be invented. **Nothing was generated.**\n\n"
        "Do one of these instead:\n"
        "- Open the ticket from that board and paste ITS url -- it looks like "
        "`https://<site>.atlassian.net/browse/KEY-123`.\n"
        "- Or describe the feature in your own words, and I'll work from that."
    )
