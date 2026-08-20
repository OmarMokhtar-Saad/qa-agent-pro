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
* ``PinnedIPTransport`` stays here, and the reason is INTERNAL and
  SUFFICIENT ON ITS OWN: this module's manual per-hop redirect follower
  builds one on EVERY hop (:func:`_fetch_one_hop`). It has NO importer
  outside this file -- ``tools/web_search.py`` and
  ``tools/jira_attachments.py`` were the last two and both were deleted
  on 2026-08-15 (batch D1). **An empty importer list is NOT evidence this
  class is unused**, and it is the substrate any revived credentialed
  fetch must reuse (docs/RETIRED_CAPABILITIES.md).

What is gone with the REST path: the Basic-Auth credential, the attachment
download + its hardcoded ``api.media.atlassian.com`` redirect allowlist (the
Atlassian MCP server returns attachment METADATA, not bytes), and the
``/rest/api/3/myself`` credential probe.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

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
    _strip_urls,
    _valid_issue_key,
    jira_mcp_required_result,
    looks_like_jira_url,
    normalize_issue_payload,
    selected_issue_key,
    verify_jira_access,
)

logger = logging.getLogger(__name__)

_RETRY_MAX = 2
_RETRY_DELAYS = (1.0, 2.0)  # seconds between attempts

# At or below this many non-whitespace characters, extracted page text is treated
# as unusable (e.g. an auth wall or a JavaScript-rendered SPA that served an empty
# server-side body — the real failure was exactly 0 chars). We refuse rather than
# hand near-empty content to the generator. Kept low to avoid rejecting short but
# legitimate pages.
_MIN_READABLE_CHARS = 20

_MAX_RAW_HTML_CHARS = 200_000  # cap raw HTML exposed to callers on very large pages

# Sentinels indicating a JS-only SPA shell (e.g. a React/Vue app that renders
# nothing without a browser) rather than a genuinely empty/broken page.
_JS_REQUIRED_SENTINELS = (
    "enable javascript",
    "you need to enable javascript",
    "please enable javascript",
    "javascript is required",
    "requires javascript",
)


# A body at/under this many chars is treated as a near-empty "shell" body — the
# regime where an "enable JavaScript" sentinel is genuine SPA evidence. A longer,
# content-rich body that merely *mentions* enabling JavaScript (a help article,
# a browser-support FAQ) is NOT an SPA shell, so the sentinel is ignored there.
_SPA_SHELL_BODY_CHARS = 200


def _looks_like_js_rendered(html: str, body_text: str) -> bool:
    """Heuristic SPA-shell detector. Never raises -- returns False on any
    inspection error.

    True only when the rendered body is a near-empty shell (<= _SPA_SHELL_BODY_CHARS)
    AND one of:
      - an explicit "enable JavaScript" sentinel appears (in the short body or the
        HTML) — e.g. SauceDemo's ~47-char noscript shell; or
      - the page ships a sizeable inline/external script bundle with no real text.

    Gating the sentinel on a short body prevents a content-rich page whose copy
    merely mentions "please enable javascript" from being misflagged as an SPA
    shell (which would discard good text and needlessly escalate to the browser
    tier). Real content pages — which also contain <script> and may mention
    JavaScript — are therefore not misflagged.
    """
    try:
        lowered_body = (body_text or "").strip().lower()
        lowered_html = (html or "").lower()
        # A content-rich body is never an SPA shell, regardless of what it mentions.
        if len(lowered_body) > _SPA_SHELL_BODY_CHARS:
            return False
        # Near-empty body: an explicit sentinel (in the short body or the raw
        # HTML/noscript) => JS-only shell. SauceDemo's shell body stays short.
        if any(
            sentinel in lowered_body or sentinel in lowered_html
            for sentinel in _JS_REQUIRED_SENTINELS
        ):
            return True
        # Or a near-empty body that ships a script bundle qualifies.
        return "<script" in lowered_html and len(lowered_html) > 1000
    except Exception:
        logger.exception("_looks_like_js_rendered: heuristic check failed")
        return False


async def fetch_url_content(url: str, jira_content: object = None) -> dict:
    """Fetch content from a Jira ticket or generic URL. Never raises — returns
    an error dict on failure.

    Jira URLs no longer produce an outbound request. Either:

    * *jira_content* was supplied (the calling agent already fetched the issue
      with its own ``mcp__atlassian__getJiraIssue`` and handed the raw JSON
      back), in which case it is normalized by
      :func:`tools.jira_mcp.normalize_issue_payload`; or
    * it was not, in which case the result carries ``needs_jira_mcp: True`` and
      an ``error`` holding the directive the agent must follow.

    Deliberately there is NO HTML-scrape fallback for a Jira host: an anonymous
    Jira Cloud page is an empty SPA shell, and generating from it fabricated
    test cases. Refusing is the safe behaviour.
    """
    try:
        hostname, _resolved_ip, block_error = await _validate_public_url(url)
        if block_error:
            return {"error": block_error, "content": None}

        if looks_like_jira_url(url):
            if jira_content:
                return normalize_issue_payload(jira_content, source_url=url)
            return jira_mcp_required_result(url)

        return await _fetch_generic(url)

    except Exception as exc:
        return {"error": str(exc), "content": None}


_LOGIN_PATHS = ("/login", "/signin", "/auth", "id.atlassian.com", "accounts.google.com")

_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


class _SSRFBlocked(Exception):
    """Raised internally when a URL (initial or post-redirect) fails the
    scheme/DNS/public-IP guard. Always caught inside _fetch_generic and
    converted to the standard {"error": ..., "content": None} contract --
    never propagates to callers."""


async def _resolve_public_ip(hostname: str) -> tuple[str | None, str | None]:
    """Resolve hostname and return (first_public_ip, error); error is None on success.

    Every resolved address must be global — a hostname with even one
    private/reserved address among its answers is rejected, matching the
    original (pre-hardening) guard's behaviour. Never raises.
    """
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(hostname, None)
    except socket.gaierror:
        return None, f"Blocked: could not resolve hostname '{hostname}'"

    first_public: str | None = None
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if not addr.is_global:
            return None, "Blocked: non-public address"
        if first_public is None:
            first_public = info[4][0]
    if first_public is None:
        return None, "Blocked: non-public address"
    return first_public, None


async def _validate_public_url(url: str) -> tuple[str | None, str | None, str | None]:
    """Validate scheme + resolve a pinned public IP for `url`.

    Returns (hostname, resolved_ip, error) — error is None on success. Shared
    by the top-level entry guard (fetch_url_content) and by every hop of
    _fetch_generic's manual redirect loop, so a redirect target gets exactly
    the same scheme/DNS/public-IP check as the original URL. Never raises.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None, None, f"Blocked: scheme '{parsed.scheme}' not allowed"
    hostname = parsed.hostname
    if not hostname:
        return None, None, "Blocked: invalid hostname"
    resolved_ip, error = await _resolve_public_ip(hostname)
    if error:
        return None, None, error
    return hostname, resolved_ip, None


class PinnedIPTransport(httpx.AsyncHTTPTransport):
    """Force a single request's connection onto a pre-validated IP.

    _validate_public_url resolves and validates `hostname` once; this
    transport then ensures the actual TCP connection goes to that exact
    address, so a second DNS lookup performed later by the HTTP stack (e.g. a
    DNS-rebinding attacker answering differently the second time) cannot
    silently redirect the connection to a private address after the check
    passed. The Host header and TLS SNI are kept as the original hostname (via
    the `sni_hostname` request extension) so virtual-hosted/SNI-routed servers
    and certificate hostname verification are unaffected.
    """

    def __init__(self, hostname: str, pinned_ip: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._hostname = hostname
        self._pinned_ip = pinned_ip

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == self._hostname:
            request.url = request.url.copy_with(host=self._pinned_ip)
            request.headers.setdefault("host", self._hostname)
            request.extensions["sni_hostname"] = self._hostname
        return await super().handle_async_request(request)


@dataclass
class _HopResult:
    """A small *detached* record of one HTTP hop.

    _fetch_one_hop reads everything it needs (status, headers, body text)
    while the httpx client/connection is still open, then returns this — so no
    live httpx.Response (and no half-open socket) ever escapes the client's
    `async with` block. Fixes the per-redirect socket/fd leak (NB-001).
    """

    status_code: int
    headers: dict
    text: str


async def _fetch_one_hop(url: str) -> _HopResult:
    """Validate + IP-pin a single request, then return a detached _HopResult.

    The httpx client is opened and fully closed inside this function; the
    response body is read (`resp.text`) BEFORE the `async with` exits, so the
    connection is released and nothing live leaks out. Raises _SSRFBlocked on
    validation failure, or propagates httpx.TransportError (handled by the
    caller's retry loop).
    """
    hostname, pinned_ip, error = await _validate_public_url(url)
    if error:
        raise _SSRFBlocked(error)
    transport = PinnedIPTransport(hostname, pinned_ip)
    async with httpx.AsyncClient(
        timeout=15, transport=transport, follow_redirects=False
    ) as client:
        resp = await client.get(
            url, headers={"User-Agent": "Mozilla/5.0 QA-Agents/1.0"}
        )
        # Read the body now, while the connection is still open, so the socket
        # is consumed/released before the client closes — never returned live.
        return _HopResult(
            status_code=resp.status_code,
            headers=dict(resp.headers),
            text=resp.text,
        )


async def _follow_redirects_with_pinning(start_url: str) -> tuple[_HopResult, str]:
    """GET start_url, manually following up to _MAX_REDIRECTS redirects.

    Every hop — including the first — is independently validated and
    IP-pinned via _fetch_one_hop, so an attacker cannot use a public URL that
    redirects to a private/internal address (e.g. the cloud metadata IP
    169.254.169.254) to reach it. Returns (hop, final_url) where `hop` is the
    detached _HopResult of the final hop and final_url is the real (un-pinned)
    URL that produced it, for callers that inspect the true final host (e.g.
    the login-page heuristic below). Intermediate hops are fully consumed and
    closed inside _fetch_one_hop, so no socket leaks per redirect (NB-001).
    Raises _SSRFBlocked if any hop is blocked or the chain exceeds
    _MAX_REDIRECTS; propagates httpx.TransportError for connection failures.
    """
    current_url = start_url
    for _ in range(_MAX_REDIRECTS + 1):
        hop = await _fetch_one_hop(current_url)
        if hop.status_code in _REDIRECT_STATUSES and "location" in hop.headers:
            current_url = urljoin(current_url, hop.headers["location"])
            continue
        return hop, current_url
    raise _SSRFBlocked(f"Blocked: exceeded {_MAX_REDIRECTS} redirects")


async def _fetch_generic(url: str) -> dict:
    """Fetch any URL and extract text via BeautifulSoup. Never raises.

    SSRF-hardened: redirects are followed manually (max _MAX_REDIRECTS hops)
    instead of via httpx's follow_redirects, re-running the full
    scheme/DNS/public-IP guard on every hop, and every request is pinned to
    its validated IP so a second DNS lookup performed by the HTTP stack cannot
    retarget the connection after the check passed (DNS rebinding).
    """
    try:
        resp = None
        final_url = url
        for attempt in range(_RETRY_MAX + 1):
            try:
                resp, final_url = await _follow_redirects_with_pinning(url)
            except _SSRFBlocked as blocked:
                logger.warning("Generic fetch blocked for %s: %s", url, blocked.args[0])
                return {"error": blocked.args[0], "content": None}
            except httpx.TransportError as exc:
                logger.warning(
                    "Generic fetch transport error (attempt %d/%d) for %s: %s",
                    attempt + 1,
                    _RETRY_MAX + 1,
                    url,
                    exc,
                )
                if attempt < _RETRY_MAX:
                    await asyncio.sleep(_RETRY_DELAYS[attempt])
                    continue
                return {
                    "error": "Could not reach the server after several attempts — please try again.",
                    "content": None,
                }
            if resp.status_code >= 500 and attempt < _RETRY_MAX:
                logger.warning(
                    "Generic fetch HTTP %d (attempt %d/%d) for %s — retrying",
                    resp.status_code,
                    attempt + 1,
                    _RETRY_MAX + 1,
                    url,
                )
                await asyncio.sleep(_RETRY_DELAYS[attempt])
                continue
            break

        if resp is None:
            return {"error": "No response after retries", "content": None}

        if resp.status_code in (401, 403):
            return {
                "error": f"HTTP {resp.status_code}: page requires authentication",
                "content": None,
            }

        if resp.status_code >= 400:
            return {
                "error": f"HTTP {resp.status_code}: request failed",
                "content": None,
            }

        if any(p in final_url for p in _LOGIN_PATHS):
            return {
                "error": "Redirected to login page — page requires authentication",
                "content": None,
            }

        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["script", "style"]):
            tag.decompose()

        title_tag = soup.find("title")
        title_text = title_tag.get_text(strip=True) if title_tag else ""

        main = soup.find("main") or soup.find("article") or soup.find("body")
        body_text = (
            main.get_text(separator="\n", strip=True)
            if main
            else soup.get_text(separator="\n", strip=True)
        )

        # A JS-only SPA shell (e.g. a React/Vue app like SauceDemo) may serve a
        # short "enable JavaScript" message that is OVER _MIN_READABLE_CHARS, so
        # detect it independently of the readable-text gate and flag spa_shell so
        # tools/ui_extractor.py can escalate to a Tier-2 browser render.
        if _looks_like_js_rendered(resp.text, body_text):
            logger.info(
                "Detected JS-rendered SPA shell for %s — flagging spa_shell for "
                "browser-render escalation",
                url,
            )
            return {
                "title": title_text,
                "description": "",
                "acceptance_criteria": "",
                "raw_text": body_text[:5000],
                "raw_html": resp.text[:_MAX_RAW_HTML_CHARS],
                "content": "",
                "spa_shell": True,
                "error": None,
            }

        # Otherwise a 200 with no readable text means an auth wall or a genuinely
        # broken/empty page — refuse rather than return empty content the
        # generator would fabricate cases from.
        if len(body_text.strip()) < _MIN_READABLE_CHARS:
            return {
                "error": (
                    "Could not extract readable content from the page — it likely "
                    "requires authentication or is rendered with JavaScript (e.g. a "
                    "Jira or other single-page app). Please paste the ticket text and "
                    "I'll generate test cases from it."
                ),
                "content": None,
            }

        return {
            "title": title_text,
            "description": body_text[:3000],
            "acceptance_criteria": "",
            "raw_text": body_text[:5000],
            "raw_html": resp.text[:_MAX_RAW_HTML_CHARS],
            "content": body_text[:5000],
            "spa_shell": False,
            "error": None,
        }
    except Exception as exc:
        return {"error": str(exc), "content": None}
