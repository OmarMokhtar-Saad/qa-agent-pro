"""Jira MCP: directive helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import logging
import secrets

from tools import settings_port
from tools.jira_mcp_connect import not_connected_message
from tools.jira_mcp_directive_parts import (
    DirectiveCtx,
    _directive_head,
    _directive_optional_steps,
    _directive_tail,
)
from tools.jira_mcp_keys import _valid_issue_key, issue_key_from_url
from tools.jira_mcp_tool_names import tool_prefix
from tools.untrusted import single_line, strip_spoof_tags

logger = logging.getLogger("tools.jira_mcp")


def _directive_target(url: str, issue_key: str) -> str:
    """The ticket the directive names: its key, or the (sanitised) URL."""
    key = _valid_issue_key(issue_key) or issue_key_from_url(url)
    # The URL is caller-supplied text going into a directive the host model
    # reads as trusted: one line, no backticks, no spoofed delimiter tags.
    shown_url = strip_spoof_tags(single_line(url, 300))
    return f"`{key}`" if key else f"the issue at `{shown_url}`"


def _directive_ctx(prefix: str, token: str) -> DirectiveCtx:
    """The per-directive settings step 1 depends on."""
    want_comments = bool(settings_port.get().jira_fetch_comments)
    # An EMPTY JIRA_AC_FIELD is a legitimate configuration -- a project may
    # simply have no Acceptance Criteria field (verified on a live
    # project: 21 fields on its Story type, none of them AC), in which case
    # criteria come from description parsing plus AC_JOB. Asking for it
    # anyway produced `...,comment,`.` -- a trailing comma and an empty field
    # name -- so the request has to drop it instead.
    ac_field = str(getattr(settings_port.get(), "jira_ac_field", "") or "").strip()
    return DirectiveCtx(prefix, token, want_comments, ac_field)


# Mirrors the qa_prepare_test_cases / qa_submit_suite boomerang: this server
# does NOT call the host's tools. It returns instructions; the agent runs them
# with its own Atlassian MCP connection and calls back with the JSON.
# Never raises; falls back to a URL-only directive when no key is parseable.
def build_fetch_directive(url: str, issue_key: str = "") -> str:
    """The DIRECTIVE returned to the calling agent for a Jira ticket URL."""
    try:
        prefix = tool_prefix()
        token = secrets.token_urlsafe(16)
        target = _directive_target(url, issue_key)
        ctx = _directive_ctx(prefix, token)
        want_parent = bool(settings_port.get().jira_fetch_parent)
        lines = _directive_head(ctx, target)
        optional = _directive_optional_steps(prefix, token, want_parent)
        lines += optional
        lines += _directive_tail(2 + len(optional), token, url)
        return "\n".join(lines)
    except Exception:
        logger.exception("build_fetch_directive failed - returning the fallback")
        return not_connected_message()


def _required_body(error: str, key: str, url: str) -> dict:
    """The fetch-shaped result dict carrying the ``needs_jira_mcp`` marker."""
    return {
        "error": error,
        "content": None,
        "needs_jira_mcp": True,
        "issue_key": key,
        "source_url": url,
    }


# Keeps ``tools/jira_fetcher.fetch_url_content``'s Hard-Rule contract
# (``{"error": ..., "content": None}``) while carrying the machine-readable
# ``needs_jira_mcp`` marker and the tester/agent-facing directive. Never raises:
# ``issue_key_from_url`` and ``build_fetch_directive`` already guard themselves,
# but this function wraps them too rather than depending transitively on that --
# a future edit to either callee must not be able to make this one leak.
def jira_mcp_required_result(url: str) -> dict:
    """The fetch-shaped result for a Jira URL that has not been supplied yet."""
    try:
        key = issue_key_from_url(url)
        return _required_body(build_fetch_directive(url, key), key, url)
    except Exception:
        logger.exception("jira_mcp_required_result failed")
        return _required_body(
            "⚠️ I couldn't build the Jira fetch directive for that "
            "URL. Paste the ticket text and I'll generate test cases from it.",
            "",
            url,
        )
