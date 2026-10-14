"""Per-hop HTTP half of the generic web fetcher: one validated, IP-pinned,
byte-bounded request and the manual redirect follower. Split out of
``tools/jira_fetcher.py`` (C5); that module re-exports every name here."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import cast
from urllib.parse import urljoin

import httpx

from tools.jira_fetch_guard import (
    PinnedIPTransport,
    _SSRFBlocked,
    _validate_public_url,
)

logger = logging.getLogger("tools.jira_fetcher")

_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)

# HARD read budget per hop (audit E1b, 2026-08-30). An order of magnitude above
# _MAX_RAW_HTML_CHARS, the only consumer of the full body, so no real page is
# affected -- what it bounds is the ALLOCATION a hostile response can force.
_MAX_BODY_BYTES = 3_000_000


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
    #: True when the body hit _MAX_BODY_BYTES and the tail was never read.
    truncated: bool = False


async def _collect_chunks(resp) -> tuple[bytes, bool]:
    """Stream the body up to _MAX_BODY_BYTES; return ``(raw, truncated)``."""
    chunks: list[bytes] = []
    total = 0
    truncated = False
    async for chunk in resp.aiter_bytes():
        if total + len(chunk) >= _MAX_BODY_BYTES:
            chunks.append(chunk[: max(_MAX_BODY_BYTES - total, 0)])
            total = _MAX_BODY_BYTES
            truncated = True
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks), truncated


def _decode_body(raw: bytes, encoding: str | None) -> str:
    """Decode *raw* with the response encoding, falling back to utf-8."""
    try:
        return raw.decode(encoding or "utf-8", errors="replace")
    except (LookupError, TypeError):
        return raw.decode("utf-8", errors="replace")


# 2026-08-30 (audit E1b): the body is read under a HARD byte budget
# while streaming, instead of being fully buffered by `resp.text` and
# sliced to _MAX_RAW_HTML_CHARS afterwards. The slice never bounded
# the ALLOCATION -- an attacker-controlled page (or a redirect chain
# ending at one) could make this process hold the whole response in
# memory before a single character was discarded. A declared
# over-budget content-length is logged, not trusted; the read stops
# at the budget either way, since the header is attacker-controlled
# too. Everything is still read BEFORE the client closes, so no live
# response and no half-open socket escapes (NB-001).
async def _read_bounded_body(resp) -> tuple[str, bool]:
    """Read and decode the streamed body; return ``(text, truncated)``."""
    raw, truncated = await _collect_chunks(resp)
    return _decode_body(raw, resp.encoding), truncated


def _warn_if_oversize(headers: dict) -> None:
    """Log (never trust) a declared content-length over the read budget."""
    declared = headers.get("content-length") or ""
    if declared.isdigit() and int(declared) > _MAX_BODY_BYTES:
        logger.warning(
            "Response declares %s bytes, over the %d-byte read budget - "
            "reading a bounded prefix only",
            declared,
            _MAX_BODY_BYTES,
        )


async def _hop_from_stream(resp) -> _HopResult:
    """Build the detached _HopResult from an open streamed response."""
    headers = dict(resp.headers)
    if resp.status_code in _REDIRECT_STATUSES and "location" in headers:
        # A redirect's body is never parsed by any caller; do not read it.
        return _HopResult(status_code=resp.status_code, headers=headers, text="")
    _warn_if_oversize(headers)
    # Everything is still read BEFORE the client closes, so no live
    # response and no half-open socket escapes (NB-001).
    text, truncated = await _read_bounded_body(resp)
    return _HopResult(
        status_code=resp.status_code,
        headers=headers,
        text=text,
        truncated=truncated,
    )


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
    transport = PinnedIPTransport(cast("str", hostname), cast("str", pinned_ip))
    async with httpx.AsyncClient(
        timeout=15, transport=transport, follow_redirects=False
    ) as client:
        async with client.stream(
            "GET", url, headers={"User-Agent": "Mozilla/5.0 QA-Agents/1.0"}
        ) as resp:
            return await _hop_from_stream(resp)


# Every hop — including the first — is independently validated and
# IP-pinned via _fetch_one_hop, so an attacker cannot use a public URL that
# redirects to a private/internal address (e.g. the cloud metadata IP
# 169.254.169.254) to reach it. Returns (hop, final_url) where `hop` is the
# detached _HopResult of the final hop and final_url is the real (un-pinned)
# URL that produced it, for callers that inspect the true final host (e.g.
# the login-page heuristic below). Intermediate hops are fully consumed and
# closed inside _fetch_one_hop, so no socket leaks per redirect (NB-001).
# Raises _SSRFBlocked if any hop is blocked or the chain exceeds
# _MAX_REDIRECTS; propagates httpx.TransportError for connection failures.
async def _follow_redirects_with_pinning(start_url: str) -> tuple[_HopResult, str]:
    """GET start_url, manually following up to _MAX_REDIRECTS redirects."""
    current_url = start_url
    for _ in range(_MAX_REDIRECTS + 1):
        hop = await _fetch_one_hop(current_url)
        if hop.status_code in _REDIRECT_STATUSES and "location" in hop.headers:
            current_url = urljoin(current_url, hop.headers["location"])
            continue
        return hop, current_url
    raise _SSRFBlocked(f"Blocked: exceeded {_MAX_REDIRECTS} redirects")
