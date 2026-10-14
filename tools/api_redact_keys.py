"""Key/header sensitivity helpers for tools.api_redact (C5 w16 leaf)."""

from __future__ import annotations

import re

HEADER_ALLOWLIST = frozenset({"content-type", "accept", "user-agent", "content-length"})

_SENSITIVE_KEY_STEMS = (
    "password",
    "passwd",
    "pwd",
    "token",
    "secret",
    "otp",
    "apikey",
    "subscriptionkey",
    "nationalid",
    "nin",
    "iqama",
    "authorization",
    "auth",
    "credential",
    "session",
    "cookie",
    "privatekey",
    "signature",
    "amzsecurity",
)

# Exact (normalized) keys whose VALUE is always a structural literal, never a
# secret, at ANY depth incl. free text: ``token_type`` = "Bearer", ``expires_in``
# = a number. These stay readable so the batch-3 renderer sees them.
# NB: ``auth`` is NOT here (rev-6 H-1) — a key-level exemption also fired in free
# text and prose, so ``auth: Basic <b64>`` leaked verbatim. The contract's auth
# BLOCK is instead protected by recursing on dict values (see redact_json_value),
# so ``{"auth": {"mode": …}}`` survives while ``auth: <secret>`` still redacts.
_STRUCTURAL_EXEMPT_KEYS = frozenset({"tokentype", "expiresin"})
# Sensitive keys whose DICT value is a structural block to recurse into (redact
# by child key) rather than flatten wholesale.
_STRUCTURAL_BLOCK_KEYS = frozenset({"auth", "credentials"})
# Inside such a block, only these child keys may carry a scalar through; any
# other scalar child is redacted, so a credential smuggled onto ``auth.value``
# or ``credentials.hint`` cannot survive (rev-6.1 H-1r).
_STRUCTURAL_CHILD_ALLOW = frozenset(
    {"mode", "kind", "flowref", "scope", "username", "name", "envvar", "method", "path"}
)


def _normalize_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def key_is_sensitive(key: object) -> bool:
    n = _normalize_key(key)
    if not n or n in _STRUCTURAL_EXEMPT_KEYS:
        return False
    return any(stem in n for stem in _SENSITIVE_KEY_STEMS)


def header_value_survives(name: object) -> bool:
    return str(name).strip().lower() in HEADER_ALLOWLIST


# Full pointer grammar — a mere ``$steps.`` PREFIX must not exempt a string that
# also carries a secret (M-1: ``"$steps.x eyJ...jwt"``).
_POINTER_RE = re.compile(
    r"^\$(?:steps\.[A-Za-z0-9_-]+\.outputs\.[A-Za-z0-9_]+"
    r"|response\.body#[/A-Za-z0-9_.-]+"
    r"|inputs\.[A-Za-z0-9_]+)$"
)


def is_pointer(value: object) -> bool:
    """A runtime pointer expression (auth chain) — never a secret (H-a). Uses a
    FULL match so a pointer prefix cannot smuggle a trailing secret (M-1)."""
    return isinstance(value, str) and _POINTER_RE.match(value) is not None


def _redact_scalar(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return 0
    if isinstance(value, str):
        return f"<redacted:string({len(value)})>"
    if value is None:
        return None
    return "<redacted:value>"


def _redact_all_leaves(value: object) -> object:
    if is_pointer(value):
        return value
    if isinstance(value, dict):
        return {k: _redact_all_leaves(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_all_leaves(v) for v in value]
    return _redact_scalar(value)
