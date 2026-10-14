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

import json  # noqa: F401
import logging
import re  # noqa: F401

from tools.api_redact_desc import (  # noqa: F401
    _DESCRIPTOR_NAME_KEYS,
    _HEADER_LIST_KEYS,
    _MARKER_PREFIX,
    _VALUE_SIBLING_KEYS,
    _already_redacted,
    _redact_descriptors,
    _strip_sample_response,
)
from tools.api_redact_entry import (  # noqa: F401
    REDACTION_ERROR_MARKER,
    redact_contract,
    redact_headers,
    redact_intake_source,
)
from tools.api_redact_keys import (  # noqa: F401
    _POINTER_RE,
    _SENSITIVE_KEY_STEMS,
    _STRUCTURAL_BLOCK_KEYS,
    _STRUCTURAL_CHILD_ALLOW,
    _STRUCTURAL_EXEMPT_KEYS,
    HEADER_ALLOWLIST,
    _normalize_key,
    _redact_all_leaves,
    _redact_scalar,
    header_value_survives,
    is_pointer,
    key_is_sensitive,
)
from tools.api_redact_text import (  # noqa: F401
    _AUTH_CRED_RE,
    _CURL_BEARER_RE,
    _CURL_HEADER_RE,
    _CURL_USER_RE,
    _PLAIN_WORD_RE,
    _VALUE_PATTERNS,
    PROSE_REDACTION_IS_BEST_EFFORT,
    _auth_cred_sub,
    _curl_header_sub,
    _redact_value_patterns,
    _try_parse_json,
    redact_text,
    scan_finds_secret,
)
from tools.api_redact_walk import (  # noqa: F401
    _MARKER_ONLY_RE,
    _SHAPE_TYPE_NAMES,
    _redact_block,
    _redact_structural,
    _redact_value_shapes_structured,
    redact_json_value,
)

logger = logging.getLogger(__name__)
