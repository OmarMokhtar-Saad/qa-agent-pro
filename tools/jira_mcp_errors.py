"""Jira MCP: errors helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from tools.jira_mcp_urls import _strip_urls

logger = logging.getLogger("tools.jira_mcp")

# Only short, sanitized FRAGMENTS of that blob are ever echoed to a tester.
_MAX_ECHO_CHARS = 200


_MAX_ERROR_ECHO_CHARS = 300


_ERROR_KEYS = ("error", "errors", "errorMessages", "errorMessage", "isError")


def _sanitize_echo(value: object, limit: int) -> str:
    """One-line, URL-free, backtick-free, length-capped rendering of an
    untrusted fragment before it is shown to a tester. Never raises."""
    with never_raise(
        logger,
        "_sanitize_echo failed - dropping the value",
        level=logging.ERROR,
        exc_info=True,
    ):
        text = "".join(
            ch
            for ch in str(value or "")
            if ch == " " or (ch.isprintable() and ch != "`")
        )
        return _strip_urls(text).strip()[:limit]
    return ""


# The reason _error_text reports when a failure key is present but carries no
# readable message. Named so a caller can tell "Jira said why" from "Jira only
# said no".
_NO_IDENTITY = "the call did not come back with an identity"


def _jira_failure_reason(payload: dict) -> str:
    """The readable Jira/MCP failure text on a payload, or "" when there is none.

    Never raises. Returns "" for the message-less case so the caller keeps its
    own wording rather than printing an identity-probe phrase at a tester.
    """
    # _error_text never raises
    with never_raise() as g:
        text = _error_text(payload if isinstance(payload, dict) else {})
    if g.failed:
        return ""
    return "" if text == _NO_IDENTITY else text


# True when the payload carries a failure MARKER at all, however empty.
#
# _error_text deliberately skips an empty value so a message-less marker does
# not print as a reason -- but ``{"error": ""}`` is exactly what this server
# ASKS an agent to send when the call failed, and reading it as success is how
# an unconnected client came to be recorded as verified. Presence is the
# signal here; the text is a separate question. Never raises.
def _carries_error_key(payload: object) -> bool:
    """True when the payload carries a failure MARKER at all, however empty."""
    # defensive
    with never_raise(logger, "_carries_error_key failed", exc_info=True):
        if not isinstance(payload, dict):
            return False
        return any(
            key in payload
            and payload.get(key) is not None
            and payload.get(key) is not False
            for key in _ERROR_KEYS
        )
    return False


def _failure_value_text(value: object) -> str:
    """The sanitized text of one failure-marker value ("" when it has none)."""
    if isinstance(value, bool):
        return ""
    if isinstance(value, list):
        return "; ".join(
            part
            for part in (
                _sanitize_echo(item, _MAX_ECHO_CHARS)
                for item in value
                if isinstance(item, str)
            )
            if part
        )
    if isinstance(value, dict):
        return _sanitize_echo(value.get("message"), _MAX_ERROR_ECHO_CHARS)
    return _sanitize_echo(value, _MAX_ERROR_ECHO_CHARS)


def _marker_is_absent(value: object) -> bool:
    """True for a failure-marker value that means "no failure"."""
    # `is False` only catches the JSON literal. A client that stringifies
    # or int-codes the flag sends "false" / 0, and reading either as a
    # FAILURE is the same over-eager mistake in the other direction.
    if value is None or value is False or value in (0, "false", "False"):
        return True
    return isinstance(value, (str, list, dict)) and not value


# Sanitized failure reason when the payload looks like a failure, else "".
#
# Recognizes the marker this server ASKS the agent for
# (``{"error": "..."}``) plus the shapes Jira / MCP themselves use
# (``errorMessages``, ``isError``). Never raises.
def _error_text(payload: dict) -> str:
    """Sanitized failure reason when the payload looks like a failure, else ""."""
    with never_raise(
        logger,
        "_error_text failed - reporting a generic failure",
        level=logging.ERROR,
        exc_info=True,
    ):
        for key in _ERROR_KEYS:
            if key not in payload or _marker_is_absent(payload.get(key)):
                continue
            return _failure_value_text(payload.get(key)) or _NO_IDENTITY
        return ""
    return _NO_IDENTITY
