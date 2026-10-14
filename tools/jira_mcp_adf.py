"""Jira MCP: adf helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

from config.settings_guard import never_raise
from tools.jira_mcp_keys import issue_key_from_url

logger = logging.getLogger("tools.jira_mcp")

_MAX_ADF_DEPTH = 200


# ADF node types that carry their whole meaning in `attrs` and have NO
# `content` list. Until 2026-08-29 every one of these extracted as "", so an
# `inlineCard` -- which is how Jira renders a pasted issue link -- vanished.
# MEASURED on TICKET-5692: the ticket held 11 attrs-only inlineCard nodes and the
# sub-story reference SSB-3539 was present in the Jira payload and absent from
# the prepare payload, so the Basic Flow the model saw read "Initiate  -> End
# with successful payment" with the referenced story silently missing.
#
# The URL is NEVER emitted. `_strip_urls` above exists on purpose and the
# untrusted/SSRF posture must not regress, so an inlineCard contributes its
# ISSUE KEY (via the same `_valid_issue_key` gate every host-submitted key
# passes) or, failing that, the host-stripped path -- and the fallback is
# dropped outright if it still looks like a URL. An unknown keyless type still
# yields "", exactly as before, because guessing at one is how a decorative
# node becomes prompt text.
_KEYLESS_MAX_CHARS = 60


def _inline_card_text(attrs: dict) -> str:
    """Issue key of an inlineCard, else its host-stripped path, else ""."""
    url = attrs.get("url")
    if not isinstance(url, str) or not url:
        return ""
    key = issue_key_from_url(url)
    if key:
        return key
    # Fallback: the path only, never the host and never the scheme.
    try:
        path = (urlparse(url).path or "").strip("/")
    except ValueError:
        return ""
    path = path[:_KEYLESS_MAX_CHARS]
    if not path or "://" in path or path.lower().startswith("http"):
        return ""
    return path


def _mention_status_text(node_type: str, attrs: dict) -> str:
    """Text of a mention or status node, without a leading "@"."""
    text = attrs.get("text")
    if not isinstance(text, str):
        return ""
    text = text.strip()
    if node_type == "mention" and text.startswith("@"):
        text = text[1:].strip()
    return text[:_KEYLESS_MAX_CHARS]


def _date_node_text(attrs: dict) -> str:
    """YYYY-MM-DD of a date node's millisecond timestamp, else ""."""
    stamp = attrs.get("timestamp")
    if not isinstance(stamp, (str, int, float)):
        return ""
    seconds = int(str(stamp).strip()) / 1000.0
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%d")


def _keyless_node_text(node: dict) -> str:
    """Readable text for an ADF node that has `attrs` but no `content`.

    Returns "" for every type not named here, which is the pre-2026-08-29
    behaviour for ALL of them. Never raises.
    """
    with never_raise(
        logger, "_keyless_node_text failed - dropping the node", exc_info=True
    ):
        node_type = node.get("type")
        attrs = node.get("attrs")
        if not isinstance(attrs, dict):
            return ""
        if node_type == "inlineCard":
            return _inline_card_text(attrs)
        if node_type in ("mention", "status"):
            return _mention_status_text(node_type, attrs)
        if node_type == "date":
            return _date_node_text(attrs)
    return ""


def _adf_join(node: dict, parts: list[str]) -> str:
    """Join the child texts of *node* with the separator its type calls for."""
    sep = (
        "\n"
        if node.get("type")
        in ("paragraph", "tableRow", "tableCell", "bulletList", "listItem")
        else " "
    )
    return sep.join(p for p in parts if p.strip())


# Recursively extract plain text from Atlassian Document Format.
#
# The Atlassian MCP server returns the SAME ADF documents the REST API did, so
# this is unchanged. Guards against pathologically deep / self-referential
# blobs by capping recursion at _MAX_ADF_DEPTH.
#
# ``truncated`` is an optional SINK: when the depth cap fires, a marker is
# appended to it. The cap is a security control and stays, but it used to be
# SILENT -- a description nested past the limit came back as "" and the caller
# could not tell that from a genuinely empty body, so a whole suite could be
# written from the summary alone with no disclosure. Default ``None`` keeps
# every existing call site byte-identical.
#
# The attrs-only branch (inlineCard / mention / status / date) is checked HERE,
# after the text branch and before the recursion, so a node that DOES carry
# content is completely unaffected.
def _extract_adf_text(node: dict, depth: int = 0, truncated: list | None = None) -> str:
    """Recursively extract plain text from Atlassian Document Format."""
    if depth > _MAX_ADF_DEPTH:
        if truncated is not None:
            truncated.append(True)
        return ""
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return node.get("text", "")
    content = node.get("content", [])
    if not content:
        keyless = _keyless_node_text(node)
        if keyless:
            return keyless
    if not isinstance(content, list):
        return ""
    parts = [_extract_adf_text(child, depth + 1, truncated) for child in content]
    return _adf_join(node, parts)


def adf_text_with_depth_flag(value: object) -> tuple[str, bool]:
    """``(text, hit_depth_cap)`` for a field that may be ADF.

    The second element is what :func:`_as_text` cannot say: whether the text is
    short because the ticket is short, or because _MAX_ADF_DEPTH stopped the
    walk. Never raises.
    """
    with never_raise(
        logger,
        "adf_text_with_depth_flag failed - dropping the value",
        level=logging.ERROR,
        exc_info=True,
    ):
        if isinstance(value, dict):
            sink: list = []
            return _extract_adf_text(value, truncated=sink), bool(sink)
        return _as_text(value), False
    return "", False


def _as_text(value: object) -> str:
    """Coerce a Jira field that may be ADF, plain text, or absent into text.

    The Atlassian MCP server may return either ADF (like REST v3) or an
    already-rendered string depending on the tool and the ``fields`` requested,
    so both are accepted. Never raises.
    """
    with never_raise(
        logger,
        "_as_text failed - dropping the value",
        level=logging.ERROR,
        exc_info=True,
    ):
        if isinstance(value, dict):
            return _extract_adf_text(value)
        if value is None:
            return ""
        return str(value)
    return ""
