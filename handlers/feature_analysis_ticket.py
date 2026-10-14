"""The Feature Analysis ticket step: read the Jira/issue URL behind the input."""

from __future__ import annotations

from handlers import _common, jira
from handlers.feature_analysis_types import FeatureAnalysisCall, _FaTicket
from tools.jira_fetcher import fetch_url_content
from tools.jira_mcp import not_connected_message


def _fa_jira_directive_reply(url_content: dict) -> str:
    """The reply for a fetch that came back unusable, else "".

    The Atlassian-MCP fetch directive (``needs_jira_mcp``) is relayed WORD FOR
    WORD (AGENTS.md hard rule) rather than analysing a ticket we could not read.
    """
    if url_content.get("needs_jira_mcp"):
        return str(url_content.get("error") or not_connected_message())
    if url_content.get("error"):
        # 2026-08-31, the same defect as the prepare path: mode="jira"
        # means the TICKET is the input, so an unreadable one is a
        # refusal. This used to fall through and analyse an empty string.
        return str(url_content.get("error"))
    return ""


async def _fa_fetch_ticket(call: FeatureAnalysisCall) -> tuple[_FaTicket, str]:
    """Read the ticket behind a URL: ``(ticket, reply)``; a reply ends the call."""
    mode, text = call.mode, call.feature_or_url
    if mode not in ("jira", "jira_mobile") or not _common._is_url(text):
        return _FaTicket(), ""
    await _common.emit(call.progress, "\U0001f517 Fetching the ticket…")
    hint = jira.jira_config_hint(text)
    if hint:
        return _FaTicket(), hint
    if call.jira_content_json:
        url_content = await fetch_url_content(text, jira_content=call.jira_content_json)
    else:
        url_content = await fetch_url_content(text)
    reply = _fa_jira_directive_reply(url_content)
    if reply:
        return _FaTicket(), reply
    return _FaTicket(url_content.get("content") or "", True), ""
