"""Address checks shared by every outbound guard in this tree.

Two modules make outbound requests on a URL a tester (or a ticket) supplied --
``tools/jira_fetcher.py`` (httpx) and ``tools/browser_renderer.py`` (headless
Chromium) -- and each resolves the hostname and refuses a non-public answer.
The check itself lives HERE rather than in either of them, because a security
guard that exists in two copies is a guard that drifts: the NAT64 hole this
module closes was present in both, and was fixed in both only because they were
audited together.

This module deliberately imports nothing but the standard library, so the
renderer (which otherwise imports no internal module at all) can use it without
pulling in httpx, bs4 or the Jira stack.
"""

from __future__ import annotations

import ipaddress
import logging

logger = logging.getLogger(__name__)

# IPv6 prefixes that CARRY an IPv4 address in their low 32 bits. `is_global`
# does not treat the two alike: it correctly rejects ::ffff:127.0.0.1 (via
# ipv4_mapped) but returns True for 64:ff9b::7f00:1, which is 127.0.0.1 behind
# a NAT64 translator -- so a hostname resolving to 64:ff9b::7f00:1 or
# 64:ff9b::a00:1 walked straight through an `is_global`-only guard and reached
# loopback or the RFC1918 range on any network running NAT64/DNS64 (every
# IPv6-only mobile carrier, and plenty of corporate networks). Both prefixes
# are checked rather than only NAT64: the mapped range is already safe on this
# interpreter, and pinning it means a future `is_global` change cannot quietly
# re-open it.
_EMBEDDED_V4_PREFIXES = (
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("::ffff:0:0/96"),
)


def embedded_v4_non_public(addr: ipaddress._BaseAddress) -> bool:
    """True when *addr* is an IPv6 address embedding a NON-public IPv4 address.

    Extracts the embedded IPv4 from the NAT64 / IPv4-mapped prefixes and re-runs
    the public-address check on it. Only ever ADDS a rejection: an address in
    neither prefix, or one embedding a genuinely public IPv4, returns False and
    the caller's own ``is_global`` verdict stands. Never raises -- an
    unparseable address is treated as non-public, because this guard fails
    closed.
    """
    try:
        if addr.version != 6:
            return False
        for prefix in _EMBEDDED_V4_PREFIXES:
            if addr in prefix:
                embedded = ipaddress.ip_address(int(addr) & 0xFFFFFFFF)
                if not embedded.is_global:
                    return True
        return False
    except Exception:
        logger.warning("embedded_v4_non_public: unparseable address, failing closed")
        return True
