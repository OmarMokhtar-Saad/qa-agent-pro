"""Jira MCP: identity helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import json
import logging

from config.settings_guard import never_raise
from tools.jira_mcp_errors import _MAX_ECHO_CHARS, _sanitize_echo

logger = logging.getLogger("tools.jira_mcp")

# The real response shape is not confirmed in this repo and varies by client and
# by Atlassian MCP version, so every lookup below is best-effort and
# STRING-only: an unfamiliar payload must yield "" (and the honest "identity
# payload received") rather than a fabricated name.
# Fields that ESTABLISH an identity, whether or not they can be rendered as a
# name. atlassianUserInfo answers with account_id and no display name at all, so
# judging verification on _identity_label alone would refuse a real connection.
#
# `key` is deliberately NOT here. A Jira ISSUE key says nothing about an account,
# and a raw getJiraIssue payload is the single likeliest wrong hand-back -- this
# same server asks for one, by name, a few paragraphs away.
_IDENTITY_PRESENCE_KEYS = (
    "account_id",
    "accountId",
    "account_type",
    "accountType",
)


# WHERE an identity may legitimately sit: the root, or one hop under one of
# these. Identity fields do arrive nested in practice -- under
# `structuredContent` (MCP 2025-06-18), under `user` -- but "nested somewhere"
# is not the same as "nested here", and treating them as the same is what let a
# Jira issue's reporter prove the caller's account.
_IDENTITY_WRAPPER_KEYS = (
    "structuredContent",
    "user",
    "account",
    "myself",
    "currentUser",
    "data",
    "result",
)


# PROSE IS NEVER EVIDENCE. An earlier revision accepted a text part that
# "mentioned an account" and subtracted the failures with a phrase denylist.
# Both halves failed: a getJiraIssue result rendered as prose verified, and so
# did "session has expired", "token was revoked" and "The team is accountable
# for this". A denylist over free English cannot be completed, and each phrase
# added makes the next miss quieter. A text part whose content PARSES to JSON
# is still read -- see _unwrap_mcp_content -- which is the shape clients
# actually send; anything else re-asks. A re-ask is recoverable, a false
# "verified" persisted for a week is not.
_IDENTITY_NAME_KEYS = ("name", "displayName", "display_name", "nickname")


_IDENTITY_EMAIL_KEYS = ("email", "emailAddress", "email_address")


def _part_json(part: object) -> dict | None:
    """The dict a text part parses to, or None. A part we cannot parse is
    SKIPPED, exactly like a part with no text: letting it escape the loop
    abandoned every later part, so a client that sends "Here is your user
    info:" before the JSON was unreadable purely because of ORDER."""
    if not isinstance(part, dict):
        return None
    text = part.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        inner = json.loads(text)
    except ValueError:
        return None
    return inner if isinstance(inner, dict) else None


def _unwrap_mcp_content(payload: dict) -> dict:
    """Unwrap ONE level of the MCP tool envelope
    (``{"content": [{"type": "text", "text": "{...}"}]}``) when that is what
    the agent handed back instead of the tool's own JSON. Returns the payload
    unchanged when it is not that shape. Never raises."""
    with never_raise(logger, "_unwrap_mcp_content: not an MCP envelope", exc_info=True):
        parts = payload.get("content")
        if not isinstance(parts, list):
            return payload
        for part in parts:
            inner = _part_json(part)
            if inner is not None:
                return inner
        return payload
    return payload


# The dicts that may legitimately HOLD the caller's identity.
#
# The root, plus one hop under a named identity wrapper. Deliberately NOT a
# walk: searching every node within four levels is what let an issue's
# reporter, a status ``name`` and a bare ``self`` URL all read as proof of the
# caller's account. Depth is not the control -- POSITION is.
#
# This is the WHOLE defence. An earlier revision also refused issue-shaped
# payloads by name (`fields`, `issues`, an issue-key regex); mutation showed
# every branch of it could be disabled with the suite green, because a
# getJiraIssue result has no account field at a position this returns. A
# redundant shape check beside a position rule only invites the belief that
# shape is what decides -- which is the belief that cost three rounds.
def _identity_candidates(payload: object) -> list:
    """The dicts that may legitimately HOLD the caller's identity."""
    # defensive
    with never_raise(logger, "_identity_candidates failed", exc_info=True):
        if not isinstance(payload, dict):
            return []
        nodes = [payload]
        for key in _IDENTITY_WRAPPER_KEYS:
            value = payload.get(key)
            if isinstance(value, dict):
                nodes.append(_unwrap_mcp_content(value))
        return nodes
    return []


def _identity_value(value: object) -> bool:
    """True for a value that can actually name an account.

    STRINGS only, and `bool` is excluded explicitly -- it is a subclass of int,
    so ``{"account_id": true}`` would otherwise establish an identity.
    """
    return (
        isinstance(value, str) and not isinstance(value, bool) and bool(value.strip())
    )


def _has_identity(payload: object) -> bool:
    """True when the CALLER's account is identified at an identity position.

    Presence, not prettiness: a payload this returns False for establishes
    nothing and must not be recorded as a verified connection. Never raises.
    """
    # defensive
    with never_raise(logger, "_has_identity failed", exc_info=True):
        for node in _identity_candidates(payload):
            if any(_identity_value(node.get(key)) for key in _IDENTITY_PRESENCE_KEYS):
                return True
            # An EMAIL identifies an account. A bare display name does not:
            # a status, a priority and a project all have `name`, and reading
            # it as identity reported "connected as High".
            if any(_identity_value(node.get(key)) for key in _IDENTITY_EMAIL_KEYS):
                return True
        return False
    return False


def _first_clean(payload: dict, keys: tuple[str, ...]) -> str:
    """The first string field under ``keys`` that survives sanitising, or ""."""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            cleaned = _sanitize_echo(value, _MAX_ECHO_CHARS)
            if cleaned:
                return cleaned
    return ""


def _identity_label(payload: dict) -> str:
    """Best-effort ``Name (email)`` for an identity payload, or "".

    STRING fields only, each sanitized, so an unfamiliar shape produces "" and
    the caller reports "identity payload received" instead of inventing a name.
    Never raises."""
    with never_raise(
        logger,
        "_identity_label failed - omitting the identity",
        level=logging.ERROR,
        exc_info=True,
    ):
        name = _first_clean(payload, _IDENTITY_NAME_KEYS)
        email = _first_clean(payload, _IDENTITY_EMAIL_KEYS)
        if name and email:
            return f"{name} ({email})"
        return name or email
    return ""
