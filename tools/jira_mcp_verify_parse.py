"""Jira MCP: verify parse helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import json

from tools.jira_mcp_hint import connect_hint_line
from tools.jira_mcp_tool_names import verify_tool_name
from tools.jira_mcp_verify_directive import verify_directive

# The verification blob is UNTRUSTED, but tiny by nature (an identity object or
# a one-line error marker), so it is capped far below MAX_PAYLOAD_BYTES before
# json.loads ever sees it.
_MAX_VERIFY_CHARS = 12000


def _too_long_result() -> tuple[str, str]:
    """The verdict for a verification blob over the size cap."""
    return (
        "unknown",
        "⚠️ **That verification result was too long to "
        f"read** (over {_MAX_VERIFY_CHARS} characters). "
        f"`{verify_tool_name()}` returns a small identity object -- "
        "re-run it and pass its raw result, or pass "
        '`atlassian_verify_json={"error": "<what happened>"}` if the '
        "call failed.\n\n" + connect_hint_line(),
    )


def _unreadable_result() -> tuple[str, str]:
    """The verdict for a blob that does not parse to a JSON object."""
    return (
        "unknown",
        "⚠️ **I couldn't read that verification result**, so "
        "I still can't confirm the Atlassian connection either way. "
        f"Re-run `{verify_tool_name()}` and pass its RAW JSON result "
        "as `atlassian_verify_json`, or pass "
        '`{"error": "<what happened>"}` if the call failed.\n\n' + connect_hint_line(),
    )


def _strip_fence(text: str) -> str:
    """*text* without a leading/trailing markdown code fence."""
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text


def _parse_verify_payload(raw: object) -> dict | tuple[str, str]:
    """The parse phase of ``verify_outcome``: the parsed dict, or its early
    ``("unknown", ...)`` verdict. The size cap runs BEFORE any parse. No own
    ``try``: ``verify_outcome`` is the boundary."""
    text = str(raw or "").strip()
    if not text:
        return ("unknown", verify_directive())
    if len(text) > _MAX_VERIFY_CHARS:
        return _too_long_result()
    try:
        payload = json.loads(_strip_fence(text))
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        return payload
    return _unreadable_result()
