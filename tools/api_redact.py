"""Redaction for API-intake content (plan §4.3).

Strategy: redact STRUCTURALLY wherever the input is parsed — headers by
allowlist, JSON/contract by key (`redact_contract`, `redact_json_value`,
`redact_headers`) — because that is the only reliable path. A regex denylist over
raw free text fails open and corrupts what it scans: across six review rounds the
generic key-gated `key: value` rules leaked a new way each round (a value token
spanning a newline and eating the next key; a sensitive parent key swallowing its
own child). Those rules were REMOVED in rev-6.2.

For genuinely unstructured PROSE (`redact_text`), redaction is BEST-EFFORT and
runs only position-independent VALUE-SHAPE patterns plus position-anchored curl
flags — nothing that can eat an adjacent key. A shapeless credential in prose is
a deliberate, disclosed miss; the reliable path for it is the structured parser
feeding `redact_contract`. The intake layer discloses this UNCONDITIONALLY (see
`PROSE_REDACTION_IS_BEST_EFFORT`), never gating on a scan that only fires on a hit.

Invariants:
* **Fail closed** — every public entry catches all and returns a withheld marker.
* **Fail safe on headers** — a value survives only for the four-name allowlist.
* **Never corrupt** — the prose pass has no key-gated rule that can span lines or
  consume an adjacent key; curl flags are anchored and quote/token-bounded.
* **Shape and type survive**, and ``$steps.``/``$response.`` pointer expressions
  (the auth chain) are exempt from value redaction.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

REDACTION_ERROR_MARKER = "<redaction-error: content withheld>"

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


def _redact_value_patterns(text: str) -> str:
    """Position-independent value-shape redaction (prose and structured leaves).
    NO curl-flag rules — those belong only to redact_text (prose), never to a
    parsed contract string, where `-u`/`-H` would corrupt legitimate text
    (rev-6.3 M-1)."""
    for name, pattern in _VALUE_PATTERNS:
        text = pattern.sub(f"<redacted:{name}>", text)
    return _AUTH_CRED_RE.sub(_auth_cred_sub, text)


_MARKER_ONLY_RE = re.compile(r"<redacted:[a-z_]+>")


def _redact_value_shapes_structured(s: str) -> str:
    """Value-shape redaction for a STRUCTURED string leaf, length-preserving.
    When the WHOLE value is a single shape (a 10-digit id that looks like a
    national id, a 16-digit reference that looks like a card), emit
    ``<redacted:string(N)>`` so the length/boundary the tester supplied survives
    (rev-6.3 M-3); a partial hit (a secret embedded in prose) keeps its context."""
    if is_pointer(s):
        return s
    # A string leaf that is ITSELF a JSON object/array (HAR postData.text,
    # Postman body.raw) is the most credential-dense part of a capture — recurse
    # it structurally rather than leave it to the value-shape scan (rev-6.4 H-2).
    nested = _try_parse_json(s)
    if nested is not None:
        try:
            return json.dumps(_redact_structural(nested), ensure_ascii=False)
        except Exception:
            return f"<redacted:string({len(s)})>"
    red = _redact_value_patterns(s)
    if red == s:
        return s
    if _MARKER_ONLY_RE.fullmatch(red):
        return f"<redacted:string({len(s)})>"
    return red


def _redact_block(value: dict) -> dict:
    """Redact a structural block (auth/credentials): a scalar child survives
    only if its key is allowlisted; every other scalar child is redacted, and
    dict children recurse. Closes the smuggle-onto-auth.value path (H-1r)."""
    out: dict = {}
    for k, v in value.items():
        if isinstance(v, (dict, list)):
            out[k] = _redact_block(v) if isinstance(v, dict) else _redact_all_leaves(v)
        elif key_is_sensitive(k) or _normalize_key(k) not in _STRUCTURAL_CHILD_ALLOW:
            out[k] = _redact_scalar(v) if not is_pointer(v) else v
        elif isinstance(v, str) and not is_pointer(v):
            # An allowlisted child still gets the value-shape scan (no curl
            # flags), so a credential smuggled onto `mode`/`kind`
            # (`mode: "Basic <b64>"`) cannot survive on the key allowlist alone.
            out[k] = _redact_value_shapes_structured(v)
        else:
            out[k] = v
    return out


def redact_json_value(value: object, *, key: object = None) -> object:
    if key is not None and key_is_sensitive(key):
        # A sensitive key naming a known structural BLOCK with a dict value is
        # recursed with a child-key allowlist so `auth: {mode, kind}` survives
        # while `auth: "<secret>"` and `auth.value: "<secret>"` are redacted.
        if isinstance(value, dict) and _normalize_key(key) in _STRUCTURAL_BLOCK_KEYS:
            return _redact_block(value)
        return _redact_all_leaves(value)
    if isinstance(value, dict):
        return {k: redact_json_value(v, key=k) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_json_value(v) for v in value]
    if isinstance(value, str) and not is_pointer(value):
        # Structured leaf: value shapes only, length-preserving, NO curl flags
        # (rev-6.3 M-1/M-3).
        return _redact_value_shapes_structured(value)
    return value


def redact_headers(headers: object) -> list[dict]:
    out: list[dict] = []
    if not isinstance(headers, list):
        return out
    for entry in headers:
        if not isinstance(entry, dict):
            continue
        item = dict(entry)
        name = str(entry.get("name", ""))
        if header_value_survives(name) and not key_is_sensitive(name):
            for vk in ("value", "example"):
                if isinstance(item.get(vk), str):
                    item[vk] = _redact_value_patterns(item[vk])
            out.append(item)
            continue
        raw = str(entry.get("value", entry.get("example", ""))).strip().lower()
        marker = (
            "<redacted:bearer>"
            if raw.startswith("bearer")
            else (
                "<redacted:basic>" if raw.startswith("basic") else "<redacted:header>"
            )
        )
        for vk in ("value", "example"):
            if vk in item:
                item[vk] = marker
        out.append(item)
    return out


def _curl_header_sub(m: re.Match) -> str:
    flag, ws, q, name, _val, qc = m.groups()
    if header_value_survives(name):
        return m.group(0)
    return f"{flag}{ws}{q}{name}: <redacted:header>{qc}"


def redact_text(text: str) -> str:
    """Best-effort free-text redaction that NEVER corrupts the payload.

    rev-6.2: the generic key-gated ``key: value`` rules were REMOVED. Over six
    review rounds they leaked in a new way every round — a value token would span
    a newline and consume the next key (so a sensitive key was skipped and leaked
    with a false all-clear), or a sensitive parent key would swallow its own
    child and destroy a structural fact. Free text now runs only:
      1. curl flag shapes (``-H "…"``, ``-u``, ``--oauth2-bearer``) — position-
         anchored and quote/token-bounded, so they cannot eat an adjacent key; and
      2. position-independent VALUE-SHAPE patterns (email, JWT, prefixed tokens,
         ``Basic``/``Bearer`` credentials, card, national id).
    A credential pasted as raw prose with NO recognizable shape (e.g.
    ``api_key: <opaque>``) is a deliberate best-effort MISS — the reliable path
    for that is the structured parser + ``redact_contract`` (plan §0.5). Callers
    MUST disclose this unconditionally for prose intake (see the module constant
    PROSE_REDACTION_IS_BEST_EFFORT)."""
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


def redact_intake_source(text: object) -> str:
    """RAW intake content, redacted BEFORE any persist. Never raises.

    STRUCTURED input (a JSON object/array — HAR, Postman, a JSON body) is parsed
    and redacted STRUCTURALLY (reliable), so a HAR's shapeless
    ``{"name":"X-Api-Key","value":"<opaque>"}`` headers do not reach the prep
    store on the best-effort prose path (rev-6.3 H-1). Genuinely unparseable
    prose falls back to the best-effort value-shape scan."""
    try:
        if not isinstance(text, str):
            raise TypeError(f"expected str, got {type(text).__name__}")
        parsed = _try_parse_json(text)
        if parsed is not None:
            structured = _redact_structural(parsed)
            return json.dumps(structured, ensure_ascii=False)
        return redact_text(text)
    except Exception:
        logger.exception("redact_intake_source failed; content withheld")
        return REDACTION_ERROR_MARKER


_VALUE_SIBLING_KEYS = ("value", "example")
# Descriptor lists whose entries are HEADERS (allowlist: redact unless the name
# is one of the four safe headers). Postman uses singular ``header``.
_HEADER_LIST_KEYS = ("headers", "header")
# The field naming a descriptor: our template uses ``name``; Postman/HAR use
# ``key`` (``{"key":"Authorization","value":…}``) — both are handled (H-1).
_DESCRIPTOR_NAME_KEYS = ("name", "key")
_MARKER_PREFIX = "<redacted:"


def _already_redacted(v: object) -> bool:
    return isinstance(v, str) and v.startswith(_MARKER_PREFIX)


def _strip_sample_response(value: object) -> object:
    if isinstance(value, dict):
        return {
            k: _strip_sample_response(v)
            for k, v in value.items()
            if k != "sample_response"
        }
    if isinstance(value, list):
        return [_strip_sample_response(v) for v in value]
    return value


def _redact_descriptors(value: object, *, header_ctx: bool = False) -> object:
    if isinstance(value, dict):
        out = {
            k: _redact_descriptors(v, header_ctx=(str(k) in _HEADER_LIST_KEYS))
            for k, v in value.items()
        }
        name = next(
            (out[k] for k in _DESCRIPTOR_NAME_KEYS if out.get(k) is not None), None
        )
        if name is not None:
            redact = (
                (not header_value_survives(name))
                if header_ctx
                else key_is_sensitive(name)
            )
            if redact:
                for sib in _VALUE_SIBLING_KEYS:
                    # Skip a value already redacted upstream — re-redacting a
                    # ``<redacted:string(10)>`` marker inflates its length and
                    # loses the boundary the tester supplied (rev-6.4 M-1).
                    if (
                        sib in out
                        and not is_pointer(out[sib])
                        and not _already_redacted(out[sib])
                    ):
                        out[sib] = _redact_scalar(out[sib])
        return out
    if isinstance(value, list):
        return [_redact_descriptors(v, header_ctx=header_ctx) for v in value]
    return value


_SHAPE_TYPE_NAMES = {
    "string",
    "number",
    "integer",
    "boolean",
    "array",
    "object",
    "null",
}


def _redact_structural(obj: object) -> object:
    """Strip sample_response (any depth), then structural redaction: key-based
    (redact_json_value) + the {name, value} descriptor allowlist. Shared by the
    contract path and the parse-first intake path (rev-6.3 H-1).

    success.response_shape is a closed TYPE vocabulary keyed by (often sensitive)
    field names; redacting its type values ("string" under key "nationalId") corrupts
    the grounding authority and makes the renderer refuse the whole suite (rev-3 H-B).
    So it is lifted out before redaction and re-attached afterwards: an entry whose
    value is a known type name survives verbatim, anything else is still redacted (a
    PHI value stuffed where a type belongs is still redacted IF its key is
    sensitive; a plain value under a non-sensitive key survives, but a non-type
    shape is refused by the renderer before any Java is produced — rev-4 L1)."""
    plain = json.loads(json.dumps(obj))
    plain = _strip_sample_response(plain)
    saved_shape = None
    if isinstance(plain, dict) and isinstance(plain.get("success"), dict):
        rs = plain["success"].get("response_shape")
        if isinstance(rs, dict):
            saved_shape = rs
            plain["success"] = {
                k: v for k, v in plain["success"].items() if k != "response_shape"
            }
    out = _redact_descriptors(redact_json_value(plain))
    if (
        saved_shape is not None
        and isinstance(out, dict)
        and isinstance(out.get("success"), dict)
    ):
        out["success"]["response_shape"] = {
            k: (
                v
                if isinstance(v, str) and v.strip().lower() in _SHAPE_TYPE_NAMES
                else redact_json_value(v, key=k)
            )
            for k, v in saved_shape.items()
        }
    return out


def redact_contract(contract: object) -> dict:
    """Redact a contract dict. Never raises; {} on failure (fail closed).
    sample_response is stripped at ANY depth — the raw sample is kept in memory
    only and never committed (H8)."""
    try:
        redacted = _redact_structural(contract)
        return redacted if isinstance(redacted, dict) else {}
    except Exception:
        logger.exception("redact_contract failed; contract withheld")
        return {}
