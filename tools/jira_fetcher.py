"""SSRF-hardened fetcher for GENERIC web content, plus Jira-URL routing.

2026-08-01 MIGRATION
--------------------
The authenticated Jira REST path (HTTP Basic Auth with ``JIRA_EMAIL`` +
``JIRA_API_TOKEN``) has been REMOVED. Jira is now read through the CALLING
AGENT's own Atlassian MCP connection (``mcp.atlassian.com``, OAuth 2.1, Jira
Cloud only) using the same boomerang shape as host-mode generation - see
``tools/jira_mcp.py``. A Jira URL that reaches :func:`fetch_url_content` no
longer triggers an outbound authenticated request and no longer falls back to
scraping the anonymous Jira SPA shell (which produced fabricated suites);
instead it returns ``needs_jira_mcp`` plus the directive the agent must act on.

What did NOT change:

* **The Hard-Rule error contract.** Nothing in this module raises to a caller.
  Every path returns ``{"error": ..., "content": None}`` on failure.
* **The SSRF hardening for generic URLs**, which is still exercised by web
  pages, Swagger specs and UI extraction: an http(s) scheme guard, a DNS +
  public-IP gate where EVERY resolved answer must be global, a pinned-IP
  transport that defeats DNS rebinding between check and connect, and MANUAL
  per-hop redirect following that re-runs the full guard on every hop.

What is gone with the REST path: the Basic-Auth credential, the attachment
download + its hardcoded ``api.media.atlassian.com`` redirect allowlist (the
Atlassian MCP server returns attachment METADATA, not bytes), and the
``/rest/api/3/myself`` credential probe.
"""

from __future__ import annotations

import asyncio  # noqa: F401
import ipaddress  # noqa: F401
import logging
import socket  # noqa: F401
from dataclasses import dataclass  # noqa: F401
from typing import cast  # noqa: F401
from urllib.parse import urljoin, urlparse  # noqa: F401

import httpx  # noqa: F401
from bs4 import BeautifulSoup  # noqa: F401

from tools.error_boundary import error_dict, guarded
from tools.jira_fetch_generic import (  # noqa: F401
    _RETRY_DELAYS,
    _RETRY_MAX,
    _fetch_generic,
    _fetch_with_retries,
)
from tools.jira_fetch_guard import (  # noqa: F401
    PinnedIPTransport,
    _resolve_public_ip,
    _SSRFBlocked,
    _validate_public_url,
)
from tools.jira_fetch_hop import (  # noqa: F401
    _MAX_BODY_BYTES,
    _MAX_REDIRECTS,
    _REDIRECT_STATUSES,
    _fetch_one_hop,
    _follow_redirects_with_pinning,
    _HopResult,
    _read_bounded_body,
)
from tools.jira_fetch_page import (  # noqa: F401
    _JS_REQUIRED_SENTINELS,
    _LOGIN_PATHS,
    _MAX_RAW_HTML_CHARS,
    _SPA_SHELL_BODY_CHARS,
    MIN_READABLE_CHARS,
    _extract_title_and_text,
    _http_refusal,
    _looks_like_js_rendered,
    _page_result,
    _unreadable_page_result,
)

# Re-exported for backwards compatibility: these helpers are PURE (no HTTP) and
# moved to tools/jira_mcp.py with the rest of the Jira logic. The importers this
# comment used to name are down to one -- router.py was deleted in P2-A
# (2026-08-15), leaving the tests. tools/comment_reconciler's docs
# named _comment_lines here too; that module was deleted on 2026-08-15
# (dead-code deletion batch D5).
from tools.jira_mcp import (  # noqa: F401
    _MAX_ADF_DEPTH,
    _build_parent_context,
    _comment_lines,
    _extract_ac_from_description,
    _extract_adf_text,
    _extract_issuelinks,
    _extract_names,
    _extract_parent_ref,
    _extract_priority,
    _extract_subtasks,
    _ParentBg,
    _strip_urls,
    _valid_issue_key,
    is_jira_host,
    jira_mcp_required_result,
    jira_page_without_issue_message,
    looks_like_jira_url,
    normalize_issue_payload,
    selected_issue_key,
    verify_jira_access,
)
from tools.net_guard import embedded_v4_non_public  # noqa: F401

logger = logging.getLogger(__name__)

# AGENTS.md Hard Rule 2 ("jira_fetcher.py ... must never raise to callers --
# always return {"error": ..., "content": None}"): the public entry points
# OTHER modules call, each of which wraps its whole body in try/except and
# returns the error-shaped dict on failure. Reviewed on export; see
# tests/test_jira_public_api_never_raises.py, the parametrized backstop that
# asserts each of these survives an injected internal exception.
PUBLIC_CALLABLES = frozenset({"fetch_url_content"})


async def validate_public_url(url: str) -> tuple[str | None, str | None, str | None]:
    """Public seam for the SSRF guard; calls the private name at call time."""
    return await _validate_public_url(url)


async def fetch_generic(url: str) -> dict:
    """Public seam for the generic fetch; calls the private name at call time."""
    return await _fetch_generic(url)


async def follow_redirects_with_pinning(start_url: str) -> tuple[_HopResult, str]:
    """Public seam for the pinned redirect walk; calls the private name."""
    return await _follow_redirects_with_pinning(start_url)


# Fetch content from a Jira ticket or generic URL. Never raises — returns
# an error dict on failure.
#
# Jira URLs no longer produce an outbound request. Either:
#
# * *jira_content* was supplied (the calling agent already fetched the issue
#   with its own ``mcp__atlassian__getJiraIssue`` and handed the raw JSON
#   back), in which case it is normalized by
#   :func:`tools.jira_mcp.normalize_issue_payload`; or
# * it was not, in which case the result carries ``needs_jira_mcp: True`` and
#   an ``error`` holding the directive the agent must follow.
#
# Deliberately there is NO HTML-scrape fallback for a Jira host: an anonymous
# Jira Cloud page is an empty SPA shell, and generating from it fabricated
# test cases. Refusing is the safe behaviour.
@guarded(error_dict)
async def fetch_url_content(url: str, jira_content: object = None) -> dict:
    """Fetch a Jira ticket or generic URL. Never raises; returns an error dict on failure."""
    hostname, _resolved_ip, block_error = await validate_public_url(url)
    if block_error:
        return {"error": block_error, "content": None}

    if looks_like_jira_url(url):
        if jira_content:
            return normalize_issue_payload(jira_content, source_url=url)
        return jira_mcp_required_result(url)

    if (refusal := _non_issue_jira_page_result(url)) is not None:
        return refusal

    return await fetch_generic(url)


# Refuse a Jira HOST URL that names no issue; ``None`` means carry on.
#
# 2026-08-31: the "no scrape of a Jira host" promise only covered URLs that
# name an ISSUE. A board, backlog, dashboard or Confluence URL on the same
# host is not ``looks_like_jira_url``, so it fell through to
# :func:`_fetch_generic`, which scraped the shell and returned its
# ``<title>`` -- "Jira" -- as the page content. A live run turned that one
# word into eight categories of test cases with no disclosure. Such a URL now
# refuses with ``jira_page_without_issue: True``.
def _non_issue_jira_page_result(url: str) -> dict | None:
    """Refuse a Jira HOST URL that names no issue; ``None`` means carry on."""
    if is_jira_host(url) and "/wiki/" not in (urlparse(url).path or ""):
        # A Jira HOST carrying no issue key: a board, backlog, dashboard or
        # the site root. Scraping it yields the SPA shell's <title>, which a
        # caller cannot tell apart from real requirements -- so this is a
        # refusal, not a degraded fetch.
        #
        # Confluence (`/wiki/...`) is deliberately EXEMPT: looks_like_jira_url
        # already documents that a Confluence page must fall through to the
        # SSRF-hardened generic fetcher, and a published space really does
        # serve readable HTML. Only the Jira APP pages fabricate.
        return {
            "error": jira_page_without_issue_message(url),
            "content": None,
            "needs_jira_mcp": False,
            "jira_page_without_issue": True,
            "source_url": url,
        }
    return None
