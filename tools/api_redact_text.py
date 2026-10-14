"""Value-shape and prose redaction for tools.api_redact (C5 w16 leaf)."""

from __future__ import annotations

import json
import logging
import re

from tools.api_redact_keys import header_value_survives

logger = logging.getLogger("tools.api_redact")

# Value-shape patterns (linear, no catastrophic backtracking).
_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}")),
    (
        "secret_token",
        re.compile(
            r"\b(?:gh[opsu]_[A-Za-z0-9]{16,}|sk_live_[A-Za-z0-9]{8,}|sk-[A-Za-z0-9]{16,}"
            r"|xox[baprs]-[A-Za-z0-9-]{8,}|AKIA[0-9A-Z]{16})\b"
        ),
    ),
    # Email: local part anchored + length-capped so it cannot backtrack O(n²) on
    # a long alphanumeric run with no '@' (C-2, the real quadratic pattern).
    (
        "email",
        re.compile(
            r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,}"
        ),
    ),
    # Card: 16 contiguous digits, OR digit groups joined by a space/hyphen. A
    # bare 13-digit epoch-ms timestamp is NOT a card (M-6).
    (
        "card",
        re.compile(r"(?<![\d-])(?:\d{16}|\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{1,4})(?![\d-])"),
    ),
    ("national_id", re.compile(r"(?<!\d)[12]\d{9}(?!\d)")),
    # Phone: require a '+' prefix. A bare 9-15 digit run is far more often an id
    # or timestamp in an API contract, and redacting it corrupts structural data
    # the shape derivation reads (M-6).
    ("phone", re.compile(r"(?<![\d+])\+\d{9,15}(?!\d)")),
)

# HTTP auth credential LITERAL: `Basic <b64>` / `Bearer <token>`, applied via a
# callback so a plain English word after the scheme is never redacted (rev-6.3
# M-2: "Bearer Authentication flow" must survive). The token is redacted only if
# it does NOT look like an ordinary word.
_AUTH_CRED_RE = re.compile(r"\b((?:Basic|Bearer)\s+)([A-Za-z0-9+/=._%-]{8,})")
# A plain (optionally capitalized, possibly hyphenated) all-lowercase word —
# "Authentication", "happy-path" — is prose, not a credential. Any digit,
# uppercase-mid, or base64 char breaks this, so real tokens still redact.
_PLAIN_WORD_RE = re.compile(r"[A-Za-z][a-z]*(?:-[a-z]+)*")


def _auth_cred_sub(m: re.Match) -> str:
    scheme, token = m.group(1), m.group(2)
    # A plain (optionally capitalized) all-lowercase word — "Authentication",
    # "knowledge" — is not a credential. An all-lowercase opaque token is the
    # documented residual best-effort miss (rev-6.3 L-3).
    if _PLAIN_WORD_RE.fullmatch(token):
        return m.group(0)
    return f"{scheme}<redacted:auth_credential>"


# curl -H / --header "Name: value" — QUOTED only. Position-anchored to the flag
# and bounded by its quotes, so (unlike a generic key:value rule) it cannot span
# lines or consume the next key. This and the -u/-oauth2 flags below are the
# ONLY structured shapes redact_text recognizes; everything else is left to the
# position-independent value-shape patterns (see redact_text).
_CURL_HEADER_RE = re.compile(
    r"""(?i)(-H|--header)(\s+)(["'])([A-Za-z0-9-]+)\s*:\s*([^"'\r\n]*)(\3)"""
)
# curl -u / --user. -u must be a real short flag: attached user:pass (has ':'),
# or followed by '=' or whitespace — so it never matches inside --url/-update.
_CURL_USER_RE = re.compile(r"(?i)(?:(?<=\s)|^)(-u(?=[= ]|\S*:)\s*|--user[ =]\s*)(\S+)")
_CURL_BEARER_RE = re.compile(r"(?i)(?:(?<=\s)|^)(--oauth2-bearer[ =]\s*)(\S+)")


def _redact_value_patterns(text: str) -> str:
    """Position-independent value-shape redaction (prose and structured leaves).
    NO curl-flag rules — those belong only to redact_text (prose), never to a
    parsed contract string, where `-u`/`-H` would corrupt legitimate text
    (rev-6.3 M-1)."""
    for name, pattern in _VALUE_PATTERNS:
        text = pattern.sub(f"<redacted:{name}>", text)
    return _AUTH_CRED_RE.sub(_auth_cred_sub, text)


def _curl_header_sub(m: re.Match) -> str:
    flag, ws, q, name, _val, qc = m.groups()
    if header_value_survives(name):
        return m.group(0)
    return f"{flag}{ws}{q}{name}: <redacted:header>{qc}"


# rev-6.2: the generic key-gated ``key: value`` rules were REMOVED. Over six
# review rounds they leaked in a new way every round — a value token would span
# a newline and consume the next key (so a sensitive key was skipped and leaked
# with a false all-clear), or a sensitive parent key would swallow its own
# child and destroy a structural fact. Free text now runs only:
#   1. curl flag shapes (``-H "…"``, ``-u``, ``--oauth2-bearer``) — position-
#      anchored and quote/token-bounded, so they cannot eat an adjacent key; and
#   2. position-independent VALUE-SHAPE patterns (email, JWT, prefixed tokens,
#      ``Basic``/``Bearer`` credentials, card, national id).
# A credential pasted as raw prose with NO recognizable shape (e.g.
# ``api_key: <opaque>``) is a deliberate best-effort MISS — the reliable path
# for that is the structured parser + ``redact_contract`` (plan §0.5). Callers
# MUST disclose this unconditionally for prose intake (see the module constant
# PROSE_REDACTION_IS_BEST_EFFORT).
def redact_text(text: str) -> str:
    """Best-effort free-text redaction that NEVER corrupts the payload."""
    text = _CURL_HEADER_RE.sub(_curl_header_sub, text)
    text = _CURL_USER_RE.sub(r"\1<redacted:basic>", text)
    text = _CURL_BEARER_RE.sub(r"\1<redacted:bearer>", text)
    return _redact_value_patterns(text)


# The prose path is best-effort by construction: the intake layer must ALWAYS
# disclose this for unstructured input, never gate the disclosure on whether a
# scan happened to fire (a shapeless secret produces no change and must not be
# reported as "clean"). This constant is the contract that replaces the old
# scan_finds_secret all-clear (rev-6.2 H-1).
PROSE_REDACTION_IS_BEST_EFFORT = True


def scan_finds_secret(text: object) -> bool:
    """Informational only: True if the value-shape scan redacted something.
    MUST NOT be used as an all-clear — a shapeless credential produces no change
    yet is not safe. Prose disclosure is unconditional (see the constant above)."""
    try:
        return isinstance(text, str) and redact_text(text) != text
    except Exception:
        logger.exception("scan_finds_secret failed")
        return True


def _try_parse_json(text: str) -> object:
    """Parse credential-dense STRUCTURED intake (HAR/Postman are JSON) so it can
    be redacted structurally instead of best-effort (rev-6.3 H-1). Returns the
    parsed dict/list, or None if it is not a JSON object/array."""
    t = text.lstrip()
    if t[:1] not in "{[":
        return None
    try:
        parsed = json.loads(t)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, (dict, list)) else None
