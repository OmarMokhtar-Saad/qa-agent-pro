"""SSRF guard for the generic web fetcher: scheme + DNS + public-IP checks and
the pinned-IP transport. Split out of ``tools/jira_fetcher.py`` (C5); that
module re-exports every name here. Nothing in this module raises to a caller
except :class:`_SSRFBlocked`, which is always caught by the fetch loop."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from typing import cast
from urllib.parse import urlparse

import httpx

from tools.net_guard import embedded_v4_non_public

logger = logging.getLogger("tools.jira_fetcher")


class _SSRFBlocked(Exception):
    """Raised internally when a URL (initial or post-redirect) fails the
    scheme/DNS/public-IP guard. Always caught inside _fetch_generic and
    converted to the standard {"error": ..., "content": None} contract --
    never propagates to callers."""


def _first_public(infos) -> tuple[str | None, str | None]:
    """Return ``(first_public_ip, error)`` for the resolved answers."""
    first_public: str | None = None
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if not addr.is_global or embedded_v4_non_public(addr):
            return None, "Blocked: non-public address"
        if first_public is None:
            first_public = cast("str", info[4][0])
    if first_public is None:
        return None, "Blocked: non-public address"
    return first_public, None


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
    return _first_public(infos)


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
