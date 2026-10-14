"""Jira MCP: normalize_issue_payload and the access probe (split from tools/jira_mcp.py)."""

from __future__ import annotations

import logging
from typing import NamedTuple

from tools import jira_mcp_payload
from tools.jira_mcp_blocks import (
    SourceText,
    _context_block,
    _image_block,
    _provenance_block,
    _source_text,
)
from tools.jira_mcp_issue_key import IssueRef, _check_issue_identity
from tools.jira_mcp_payload import _unwrapped_issue
from tools.jira_mcp_payload_results import (
    _empty_source_result,
    _load_error_result,
    _no_fields_result,
    _payload_failure_result,
)
from tools.jira_mcp_text_block import _text_block

logger = logging.getLogger("tools.jira_mcp")

# AGENTS.md Hard Rule 2 ("jira_fetcher.py and jira_mcp.py must never raise to
# callers -- always return {"error": ..., "content": None}"): the public
# entry points OTHER modules call. normalize_issue_payload and
# jira_mcp_required_result are fetch-shaped (return the error dict on
# failure); verify_jira_access is a network-free stand-in that always returns
# {"ok": True, ...} and cannot fail. Reviewed on export; see
# tests/test_jira_public_api_never_raises.py, the parametrized backstop that
# asserts each fetch-shaped callable survives an injected internal exception.
PUBLIC_CALLABLES = frozenset(
    {"normalize_issue_payload", "jira_mcp_required_result", "verify_jira_access"}
)


class _Parsed(NamedTuple):
    """The resolved issue identity and its source text."""

    ref: IssueRef
    src: SourceText


def _assemble(fields: dict, payload: dict, parsed: _Parsed) -> dict:
    """The grounded result dict built from the accepted payload."""
    ref, src = parsed
    title = src.summary or ref.key or "Jira issue"
    text = _text_block(fields, title, src)
    return {
        **text,
        **_image_block(fields, src.description, src.description_truncated),
        **_context_block(fields, payload, ref.key),
        **_provenance_block(fields, ref, text["raw_text"]),
    }


# The phase helpers have no try/except of their own: every one is called
# inside the single ``try`` of normalize_issue_payload, which is the
# never-raise boundary, and every refusal they build is returned from here.
def _normalized(raw: object, source_url: str) -> dict:
    """The result dict, or a refusal dict from one of the phase helpers."""
    payload, load_error = jira_mcp_payload.load_payload(raw)
    if load_error:
        return _load_error_result(load_error, source_url)
    payload, issue, fields = _unwrapped_issue(payload)
    if not fields:
        return _no_fields_result(payload, issue)
    ref = _check_issue_identity(issue, source_url)
    if isinstance(ref, dict):
        return ref
    src = _source_text(fields)
    if not (src.summary or src.description or src.ac):
        return _empty_source_result(src.any_truncated)
    return _assemble(fields, payload, _Parsed(ref, src))


# Turn a host-submitted Atlassian MCP issue payload into the grounded dict the
# REST path used to return.
#
# Returns EXACTLY the historical key set so every downstream consumer
# (``ground_and_gate``, ``_prepare_generation``, ``rtm``) is unchanged:
# ``title, description, acceptance_criteria, priority, labels, components,
# comments, comments_meta, images, parent, subtasks, issuelinks,
# parent_context, raw_text, content`` (plus ``error``).
#
# UNTRUSTED input: size-capped, ``json.loads``-only, issue keys regex-gated,
# URLs stripped from BACKGROUND text. Never raises - a malformed payload comes
# back as ``{"error": <actionable text>, "content": None}``.
#
# The phase helpers above have no try/except of their own: every one is called
# inside the single ``try`` below, which is the never-raise boundary, and every
# refusal they build is returned from here.
def normalize_issue_payload(raw: object, source_url: str = "") -> dict:
    """Turn a host-submitted Atlassian MCP issue payload into the grounded dict
    the REST path used to return.
    """
    try:
        return _normalized(raw, source_url)
    except Exception:
        logger.exception("normalize_issue_payload failed")
        return _payload_failure_result()


# Network-free stand-in for the removed REST credential probe. The server no
# longer holds Jira credentials, so there is nothing here to verify: whether
# Jira is reachable is a property of the CALLING AGENT's Atlassian MCP
# connection, which a stdio subprocess cannot see. Returning ``ok=True`` lets
# the pre-flight fall through to the fetch, where :func:`build_fetch_directive` /
# :func:`not_connected_message` provide the real, actionable guidance in one place.
# The signature is preserved so existing call sites keep working. Arguments
# are accepted and ignored. Never raises, makes no HTTP request.
async def verify_jira_access(
    *,
    base_url: str = "",
    email: str = "",
    api_token: str = "",
) -> dict:
    """Network-free stand-in for the removed REST credential probe."""
    return {"ok": True, "error": "", "account": "", "mode": "mcp"}
