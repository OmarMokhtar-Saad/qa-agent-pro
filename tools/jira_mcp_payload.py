"""Jira MCP: payload helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import json
import logging

from tools.jira_mcp_identity import _unwrap_mcp_content
from tools.jira_mcp_nodes import _issue_fields as issue_fields

logger = logging.getLogger("tools.jira_mcp")

# Host-submitted JSON is untrusted: refuse anything absurd before parsing it.
MAX_PAYLOAD_BYTES = 2_000_000


def _too_large() -> str:
    """The refusal text for a payload over ``MAX_PAYLOAD_BYTES``."""
    return (
        "That Jira payload is too large to accept "
        f"(over {MAX_PAYLOAD_BYTES // 1000} KB). Re-fetch the issue "
        "requesting only the fields listed in the directive."
    )


def _dict_payload(raw: dict) -> tuple[dict, str]:
    """A parsed dict skipped the cap a JSON string gets; size it the same."""
    size = len(json.dumps(raw, default=str).encode("utf-8", "ignore"))
    if size > MAX_PAYLOAD_BYTES:
        return {}, _too_large()
    return raw, ""


def _strip_json_fence(text: str) -> str:
    """Drop a leading ```json fence line and its closing fence, if present."""
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text


def _text_payload(raw: object) -> tuple[dict, str]:
    """Parse a JSON string submission (json.loads only), size-capped first."""
    text = str(raw or "").strip()
    if not text:
        return {}, "empty"
    if len(text.encode("utf-8", "ignore")) > MAX_PAYLOAD_BYTES:
        return {}, _too_large()
    data = json.loads(_strip_json_fence(text))
    if not isinstance(data, dict):
        return {}, "not_object"
    return data, ""


# Accepts a dict (already parsed by the MCP layer) or a JSON string, tolerating
# a ```json fence. Uses json.loads ONLY - never eval - and refuses anything
# over MAX_PAYLOAD_BYTES before parsing.
def load_payload(raw: object) -> tuple[dict, str]:
    """(payload, error) from an untrusted host submission. Never raises."""
    try:
        return _dict_payload(raw) if isinstance(raw, dict) else _text_payload(raw)
    except json.JSONDecodeError:
        return {}, "invalid_json"
    except Exception:
        logger.exception("load_payload failed")
        return {}, "invalid_json"


def _unwrapped_issue(payload: dict) -> tuple[dict, object, dict]:
    """``(payload, issue, fields)`` after the one-level MCP-envelope fallback."""
    # Some clients return the MCP envelope rather than the tool's result.
    # Unwrap ONE level -- but ONLY as a fallback, when the payload does not
    # already carry an issue. _unwrap_mcp_content returns the inner object
    # whenever ANY top-level content[*].text parses as JSON, discarding the
    # rest, so applying it unconditionally turned two payloads that generate
    # today into refusals: one carrying a top-level `content` list ALONGSIDE
    # a real issue, and a flattened one whose ADF text node happened to be
    # JSON-shaped (an API ticket pasting a snippet). Guarding on the happy
    # path costs one condition and makes this a true no-op everywhere else.
    if not issue_fields(
        payload.get("issue") if isinstance(payload.get("issue"), dict) else payload
    ):
        payload = _unwrap_mcp_content(payload)
    issue = payload.get("issue") if isinstance(payload.get("issue"), dict) else payload
    return payload, issue, issue_fields(issue)
