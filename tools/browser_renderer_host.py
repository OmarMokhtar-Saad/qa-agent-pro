"""SSRF host checks for tools/browser_renderer.py: target parsing, DNS pin."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from typing import cast
from urllib.parse import urlparse

from tools.net_guard import embedded_v4_non_public

logger = logging.getLogger("tools.browser_renderer")


def _host_resolver_rules(hostname: str, validated_ip: str) -> str:
    """Build a Chromium ``--host-resolver-rules`` value pinning `hostname`
    (and its :443 form) to the already-validated public IP.

    This forces Chromium to reuse the IP the SSRF guard checked instead of
    performing its own second DNS lookup, so a DNS-rebinding attacker cannot
    answer "public" for the guard and "169.254.169.254 / 10.x / 127.0.0.1" for
    Chromium moments later (NB-002).
    """
    return (
        f"MAP {hostname} {validated_ip},"
        f"MAP {hostname}:443 {validated_ip}:443,"
        f"MAP {hostname}:80 {validated_ip}:80,"
        # Everything else fails to resolve: a redirect or subresource to any
        # OTHER host cannot be looked up (it would otherwise be resolved
        # UNpinned, before the post-nav final-URL SSRF re-check runs). The
        # explicit MAPs above win because Chromium applies the first matching
        # rule; ``~NOTFOUND`` forces ERR_NAME_NOT_RESOLVED for the rest.
        "MAP * ~NOTFOUND"
    )


def _parse_target(url: str) -> tuple[str | None, str | None]:
    """Return ``(hostname, error)`` for the scheme and hostname checks."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None, f"Blocked: scheme '{parsed.scheme}' not allowed"
    if not parsed.hostname:
        return None, "Blocked: invalid hostname"
    return parsed.hostname, None


async def _resolve(hostname: str) -> list | None:
    """Resolve `hostname`; None when it does not resolve."""

    try:
        loop = asyncio.get_running_loop()
        return await loop.getaddrinfo(hostname, None)
    except socket.gaierror:
        return None


def _first_public_ip(infos: list) -> str | None:
    """First address when EVERY resolved address is public, else None."""
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        # Same NAT64 / IPv4-mapped gap as tools/jira_fetcher's guard, and
        # the same helper closes it -- shared rather than duplicated, so
        # the two SSRF guards cannot drift apart.
        if not addr.is_global or embedded_v4_non_public(addr):
            return None
    return cast("str", infos[0][4][0]) if infos else None


async def pin_public_ip(hostname: str) -> tuple[str | None, str | None]:
    """Resolve `hostname`; return ``(validated_ip, error)``."""
    infos = await _resolve(hostname)
    if infos is None:
        return None, f"Blocked: could not resolve hostname '{hostname}'"
    ip = _first_public_ip(infos)
    if ip is None:
        return None, "Blocked: non-public address"
    return ip, None
