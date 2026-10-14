"""Generic-URL fetch with retries. Split out of ``tools/jira_fetcher.py`` (C5);
that module re-exports every name here."""

from __future__ import annotations

import asyncio
import logging

import httpx

from tools.error_boundary import error_dict, guarded
from tools.jira_fetch_guard import _SSRFBlocked
from tools.jira_fetch_hop import _follow_redirects_with_pinning, _HopResult
from tools.jira_fetch_page import _http_refusal, _page_result

logger = logging.getLogger("tools.jira_fetcher")

_RETRY_MAX = 2
_RETRY_DELAYS = (1.0, 2.0)  # seconds between attempts


async def _transport_failed(url: str, exc: Exception, attempt: int) -> dict | None:
    """Log a transport error; sleep and return ``None`` to retry, else the error dict."""
    logger.warning(
        "Generic fetch transport error (attempt %d/%d) for %s: %s",
        attempt + 1,
        _RETRY_MAX + 1,
        url,
        exc,
    )
    if attempt < _RETRY_MAX:
        await asyncio.sleep(_RETRY_DELAYS[attempt])
        return None
    return {
        "error": "Could not reach the server after several attempts — please try again.",
        "content": None,
    }


async def _one_try(url: str, attempt: int) -> tuple[_HopResult, str] | dict | None:
    """One attempt: ``(hop, final_url)`` or an error dict to return, ``None`` to retry."""
    try:
        resp, final_url = await _follow_redirects_with_pinning(url)
    except _SSRFBlocked as blocked:
        logger.warning("Generic fetch blocked for %s: %s", url, blocked.args[0])
        return {"error": blocked.args[0], "content": None}
    except httpx.TransportError as exc:
        return await _transport_failed(url, exc, attempt)
    if resp.status_code >= 500 and attempt < _RETRY_MAX:
        logger.warning(
            "Generic fetch HTTP %d (attempt %d/%d) for %s — retrying",
            resp.status_code,
            attempt + 1,
            _RETRY_MAX + 1,
            url,
        )
        await asyncio.sleep(_RETRY_DELAYS[attempt])
        return None
    return resp, final_url


async def _fetch_with_retries(url: str) -> tuple[_HopResult, str] | dict:
    """Run the retrying redirect-following fetch for *url*.

    Returns the ``(response, final_url)`` pair to carry on with, or an error
    dict to return as-is (the caller tells them apart with ``isinstance(r, dict)``).
    """
    for attempt in range(_RETRY_MAX + 1):
        outcome = await _one_try(url, attempt)
        if outcome is not None:
            return outcome
    return {"error": "No response after retries", "content": None}


@guarded(error_dict)
async def _fetch_generic(url: str) -> dict:
    """Fetch any URL and extract text via BeautifulSoup. Never raises.

    SSRF-hardened: redirects are followed manually (max _MAX_REDIRECTS hops)
    instead of via httpx's follow_redirects, re-running the full
    scheme/DNS/public-IP guard on every hop, and every request is pinned to
    its validated IP so a second DNS lookup performed by the HTTP stack cannot
    retarget the connection after the check passed (DNS rebinding).
    """
    fetched = await _fetch_with_retries(url)
    if isinstance(fetched, dict):
        return fetched
    resp, final_url = fetched

    if (refusal := _http_refusal(resp, final_url)) is not None:
        return refusal

    return _page_result(url, resp)
