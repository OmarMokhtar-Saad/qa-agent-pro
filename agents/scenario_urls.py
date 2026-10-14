"""Product-URL discovery in ticket text."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

logger = logging.getLogger("agents.test_scenario_agent")


_URL_IN_TEXT_RE = re.compile(r"https?://[^\s)\]>\"'}]+")


def _url_host(url: str) -> str:
    """Lower-cased hostname of *url*, or "" when it has none. Never raises."""
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _is_jira_ticket_url(url: str) -> bool:
    """True for an Atlassian/Jira issue-tracker SOURCE URL (mirrors the host
    detection in handlers.jira_config_hint).

    Such a URL documents requirements; it is NOT the application under test and
    must never become a navigation target (TICKET-7154). NOTE: only Atlassian/Jira
    hosts are recognised here — other trackers (GitHub Issues, Linear, etc.)
    still fall through to the older _scope_feature_text phrasing (documented
    follow-up). Never raises.
    """
    host = _url_host(url)
    if not host:
        return False
    return "atlassian.net" in host or host.startswith("jira.") or ".jira." in host


def _ticket_blob(url_content: dict) -> str:
    return " ".join(
        str(url_content.get(k, "") or "")
        for k in ("raw_text", "description", "acceptance_criteria", "title")
    )


# Candidate application URLs mentioned INSIDE the ticket content.
#
# Scans description / acceptance_criteria / raw_text / title for http(s) URLs,
# excluding the Jira source host itself and any other tracker link. An empty
# list means the ticket names no product URL — i.e. a backend/documentation
# story with no navigable screen. The returned URL is UNTRUSTED (ticket body
# is attacker-controllable) — the caller must wrap it, never assert it as
# fact. Never raises.
def _find_product_urls(url_content: dict, source_url: str) -> list[str]:
    """Candidate application URLs mentioned inside the ticket content."""
    try:
        source_host = _url_host(source_url)
        found: list[str] = []
        for raw in _URL_IN_TEXT_RE.findall(_ticket_blob(url_content)):
            candidate = raw.rstrip(".,);]")
            host = _url_host(candidate)
            if not host or host == source_host or _is_jira_ticket_url(candidate):
                continue
            if candidate not in found:
                found.append(candidate)
        return found
    except Exception:
        logger.exception("_find_product_urls failed — assuming none")
        return []
