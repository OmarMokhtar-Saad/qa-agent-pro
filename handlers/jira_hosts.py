"""Jira host detection and the "no access" message."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from config.settings_guard import never_raise
from tools import settings_port
from tools.jira_mcp import connect_steps

logger = logging.getLogger("tools.mcp_handlers")


def _safe_host(raw) -> str:
    """Hostname of *raw* when it is a URL, else "". Never raises."""
    try:
        return urlparse(str(raw or "")).hostname or ""
    except ValueError:
        return ""


def _looks_like_jira_host(url: str) -> bool:
    """True when a pasted URL's host looks like a Jira / Atlassian instance.
    Shared by the config hint and the access pre-flight. Never raises."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    return "atlassian.net" in host or host.startswith("jira.") or ".jira." in host


# True when scraping *url* for app UI structure cannot pay off (ops-4b).
#
# extract_ui_elements exists to read the structure of the APP UNDER TEST. A
# Jira link is not that app -- the page is the TICKET -- so the call returns
# "0 headings, 0 fields, 0 buttons, 0 links (method=none)" and contributes
# nothing: _build_ui_prompt_block already renders "" for zero elements and
# _complexity_signal_score already scores 0. Skipping is therefore behaviour-
# preserving, and it replaces a log line that reads like a failed extraction
# with one that states the real reason.
#
# Detection deliberately has TWO arms: _looks_like_jira_host covers the
# atlassian.net / jira.* shapes, and a match against the configured
# JIRA_BASE_URL host covers a self-hosted instance on a custom domain
# (tickets.example.com), which the shape check alone would miss.
#
# A MOBILE-only run never reaches the caller's extraction block at all (it is
# guarded by _is_url and there is no URL), so *attached_images* is accepted
# and documented rather than gated on: a real web-app URL submitted WITH
# screenshots still has a live page worth extracting, and skipping there would
# discard useful signal. Jira + mobile is covered by the Jira arm.
#
# Never raises: an unparseable URL returns False and falls through to the
# normal extraction path, so this can only ever skip known-useless work.
def _skip_ui_extraction(url: str, attached_images: list | None = None) -> bool:
    """True when scraping *url* for app UI structure cannot pay off (ops-4b)."""
    with never_raise(
        logger, "_skip_ui_extraction check failed -- extracting", exc_info=True
    ):
        if _looks_like_jira_host(url):
            return True
        host = (urlparse(url).hostname or "").lower()
        configured = (
            urlparse(settings_port.get().jira_base_url).hostname or ""
        ).lower()
        return bool(host and configured and host == configured)
    return False


# Since 2026-08-01 Jira is read through the calling agent's own Atlassian MCP
# connection, so the old "create an API token" steps would send the tester to
# a page whose output this server cannot use. Delegates to the single source
# of truth in tools/jira_mcp so all four supported clients stay documented in
# exactly one place. *verify_error* (already sanitized) is an optional
# one-line reason. Never echoes a secret; never raises.
def _jira_token_steps(host: str, *, verify_error: str = "") -> str:
    """Professional "no access" message + how to CONNECT (no API token any more)."""
    reason = f"\n\n_Reason: {verify_error}_" if verify_error else ""
    return (
        f"⚠️ **I don't have access to this Jira instance (`{host}`).**"
        + reason
        + "\n\n"
        + connect_steps()
    )
