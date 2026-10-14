"""Jira MCP: urls helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from tools import settings_port
from tools.jira_mcp_keys import selected_issue_key

logger = logging.getLogger("tools.jira_mcp")

# Every http(s) URL is stripped out of parent/linked-issue text before it
# reaches a prompt. That text is authored by OTHER people, so it is a strictly
# higher injection/phishing risk than the target ticket, and parent context is
# BACKGROUND ONLY -- it must never supply a navigation target (TICKET-7154).
_URL_IN_TICKET_RE = re.compile(r"https?://\S+")


# True when *url* names a Jira ISSUE: a Jira-Cloud-looking host (or the
# configured JIRA_BASE_URL host) AND a path/query that actually identifies an
# issue - ``/browse/``, ``/issues/`` or ``?selectedIssue=``.
#
# The path/query gate is a precondition, not a nicety: the same Atlassian host
# also serves Confluence (``/wiki/spaces/...``), dashboards, board and profile
# pages. Those are ordinary web pages, so they must FALL THROUGH to the
# SSRF-hardened generic fetcher rather than being answered with the "fetch this
# ticket with your Atlassian MCP" directive for a ticket that does not exist.
# Never raises.
def looks_like_jira_url(url: str) -> bool:
    """True when *url* names a Jira ISSUE (Jira host plus an issue path)."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    if not is_jira_host(url):
        return False
    path = (parsed.path or "").rstrip("/")
    if "/browse/" in path or "/issues/" in path:
        return True
    return bool(selected_issue_key(url))


# True when *url*'s HOST is Jira (Cloud, or the configured JIRA_BASE_URL
# host), whether or not the path names an issue.
#
# Split out of :func:`looks_like_jira_url` on 2026-08-31. That function
# deliberately answers a narrower question -- "does this name a TICKET?" -- and
# everything else on the host fell through to the SSRF-hardened generic
# fetcher. For a Jira host that is wrong in a way no tester can see: the fetch
# SUCCEEDS, returns the SPA shell, and its ``<title>`` becomes the feature
# description. Callers use this to refuse instead of scrape. Never raises.
def is_jira_host(url: str) -> bool:
    """True when *url*'s HOST is Jira, whether or not the path names an issue."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    if (
        host == "atlassian.net"
        or host.endswith(".atlassian.net")
        or host.startswith("jira.")
        or ".jira." in host
    ):
        return True
    configured = _configured_jira_host()
    return bool(configured) and host == configured


def _configured_jira_host() -> str:
    """Lower-cased host of the configured Jira base URL, or "" when unusable."""
    try:
        return (
            urlparse(settings_port.get().jira_base_url or "").hostname or ""
        ).lower()
    except (AttributeError, TypeError, ValueError):
        return ""


def _strip_urls(text: str) -> str:
    """Replace every http(s) URL in *text* with a placeholder. Never raises."""
    try:
        return _URL_IN_TICKET_RE.sub("[link removed]", text)
    except Exception:
        logger.exception("_strip_urls failed - dropping the text")
        return ""
