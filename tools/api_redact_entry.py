"""Public redaction entry points for tools.api_redact (C5 w16 leaf)."""

from __future__ import annotations

import json
import logging

from tools.api_redact_keys import header_value_survives, key_is_sensitive
from tools.api_redact_text import _redact_value_patterns, _try_parse_json, redact_text
from tools.api_redact_walk import _redact_structural

logger = logging.getLogger("tools.api_redact")

REDACTION_ERROR_MARKER = "<redaction-error: content withheld>"


def _header_marker(entry: dict) -> str:
    raw = str(entry.get("value", entry.get("example", ""))).strip().lower()
    if raw.startswith("bearer"):
        return "<redacted:bearer>"
    return "<redacted:basic>" if raw.startswith("basic") else "<redacted:header>"


def _redact_header_entry(entry: dict) -> dict:
    item = dict(entry)
    name = str(entry.get("name", ""))
    if header_value_survives(name) and not key_is_sensitive(name):
        for vk in ("value", "example"):
            if isinstance(item.get(vk), str):
                item[vk] = _redact_value_patterns(item[vk])
        return item
    marker = _header_marker(entry)
    for vk in ("value", "example"):
        if vk in item:
            item[vk] = marker
    return item


def redact_headers(headers: object) -> list[dict]:
    if not isinstance(headers, list):
        return []
    return [_redact_header_entry(e) for e in headers if isinstance(e, dict)]


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
