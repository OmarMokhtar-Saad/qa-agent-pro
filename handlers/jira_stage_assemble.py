"""Redeem staged Jira parts: token shape guards, payload assembly and the refusal text."""

from __future__ import annotations

import json
import logging

from config.settings_guard import never_raise
from handlers.jira_stage_tray import _JIRA_STAGE_TRAY
from tools.jira_mcp import fetch_tool_name

logger = logging.getLogger("tools.mcp_handlers")

#: A stage token is minted by ``jira_mcp.build_fetch_directive`` as
#: ``secrets.token_urlsafe(16)``, so it is always URL-safe base64. Anything
#: else reaching a reply is host-supplied free text. Checked without ``re``:
#: this module has no module-level regex import and does not need one for a
#: character-class test.
_STAGE_TOKEN_EXTRA_CHARS = "-_"


def _is_stage_token_shaped(text: str) -> bool:
    """True when ``text`` could be a minted stage token: URL-safe base64, and
    long enough that no short free-text fragment passes by accident."""
    return 16 <= len(text) <= 64 and all(
        (ch.isascii() and ch.isalnum()) or ch in _STAGE_TOKEN_EXTRA_CHARS for ch in text
    )


def _safe_stage_token(value: object) -> str:
    """``value`` if it is shaped like a stage token, else a sanitised stand-in.

    Same discipline as ``host_mode.safe_prep_id`` and ``jira_mcp._sanitize_echo``:
    a token we do not recognise is still ECHOED -- a host that sent a typo needs
    to see what it sent -- but stripped of backticks and newlines and capped, so
    it can only ever read as a wrong token, never close its code span and write
    markdown into text the model then follows. Never raises.
    """
    with never_raise():
        text = str(value or "").strip()
        if _is_stage_token_shaped(text):
            return text
        flattened = "".join(
            " " if ch in "`\n\r\t" else ch for ch in text[:64] if ch.isprintable()
        )
        return " ".join(flattened.split())
    return ""


def _assemble_staged_jira_payload(stage_token: str) -> tuple:
    """(assembled_json_string, error) from the parts staged under
    stage_token. Requires at least `issue`; a malformed single part does not
    sink the others. Never raises."""
    with never_raise(logger, "_assemble_staged_jira_payload failed", exc_info=True):
        token = str(stage_token or "").strip()
        entry = _JIRA_STAGE_TRAY.get(token) if token else None
        if not entry or not entry.get("issue"):
            return "", _no_issue_staged_message(token, entry)
        payload: dict = {}
        try:
            payload["issue"] = json.loads(entry["issue"])
        except (ValueError, TypeError, KeyError):
            return "", "The staged `issue` part is not valid JSON -- re-stage it."
        _add_optional_staged_parts(payload, entry)
        return json.dumps(payload), ""
    return "", "Could not assemble the staged Jira payload -- re-stage and retry."


# B1/B2 (2026-09-26, v1.98 scope B): the live TICKET-5645 failure was
# an agent that read this exact refusal, concluded -- wrongly, two
# turns after using the SAME bridge for part='parent'/'siblings' --
# that its Atlassian MCP tool was "not in shell", and rebuilt its
# own MCP client instead of making one more call it already had.
# Lead with an explicit denial of that belief, name the real tool,
# and give the literal next call with THIS token filled in.
def _used_bridge_note(entry: dict | None) -> str:
    """The note naming the parts already staged through the same bridge."""
    _also = [k for k in ("parent", "siblings") if entry and entry.get(k)]
    return (
        " -- the SAME bridge you already used for this ticket's " + " and ".join(_also)
        if _also
        else ""
    )


def _no_issue_staged_message(token: str, entry: dict | None) -> str:
    """The refusal text for a stage token with no `issue` part staged."""
    return (
        "⚠️ **No `issue` part staged yet -- this is not a "
        "missing tool.** You already have `"
        + fetch_tool_name()
        + "`"
        + _used_bridge_note(entry)
        + ". Call it once more for the ticket itself, then stage "
        "that exact result with this literal call: "
        f"`qa_stage_jira(stage_token='{_safe_stage_token(token)}', part='issue', "
        "json=<json.dumps(that result)>)`. Once `issue` is staged, "
        "call `qa_prepare_test_cases` again with the SAME "
        "`feature_or_url` and this SAME `stage_token` -- do not pass "
        "`jira_content_json`."
    )


def _add_optional_staged_parts(payload: dict, entry: dict) -> None:
    """Add the parent and sibling parts that parse; a bad one is skipped."""
    for part, key in (("parent", "parent_issue"), ("siblings", "sibling_issues")):
        if entry.get(part):
            try:
                payload[key] = json.loads(entry[part])
            except (ValueError, TypeError, KeyError):
                logger.debug("staged %s part failed to parse", part, exc_info=True)


def _resolve_jira_content_json(jira_content_json: str, stage_token: str) -> tuple:
    """(resolved_jira_content_json, error) -- assembles from the stage tray
    only when the caller sent stage_token AND no explicit jira_content_json
    (an explicit argument always wins, so a legacy caller's jira_content_json
    is never silently overridden). Never raises."""
    with never_raise(logger, "_resolve_jira_content_json failed", exc_info=True):
        explicit = str(jira_content_json or "").strip()
        if explicit or not stage_token:
            return jira_content_json, ""
        return _assemble_staged_jira_payload(stage_token)
    return jira_content_json, ""
