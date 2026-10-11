"""The capture tier vocabulary (plus the pinned refusal code), shared by the
capture lane and the evidence lane without either importing the other.

Stdlib-only leaf: ``tools.mobile_shared`` imports neither capture nor evidence.
"""

from __future__ import annotations

#: The tier vocabulary. A caller branches on these by NAME.
TIER_NONE = "none"
TIER_PROXIED = "proxied"
TIER_DECRYPTED = "decrypted"
TIER_TLS_FAILURE = "tls_failure"
TIER_INCONCLUSIVE = "inconclusive"
TIERS = frozenset(
    {
        TIER_NONE,
        TIER_PROXIED,
        TIER_DECRYPTED,
        TIER_TLS_FAILURE,
        TIER_INCONCLUSIVE,
    }
)

REASON_PINNED = "pinned"
