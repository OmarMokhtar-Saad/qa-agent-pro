"""Jira MCP: media helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import logging
import re

from tools import settings_port
from tools.jira_mcp_adf import _MAX_ADF_DEPTH

logger = logging.getLogger("tools.jira_mcp")

# ADF MEDIA NODES (2026-08-09). An image pasted INTO a description or a comment
# is not only an `attachment` row: the ADF document carries a `mediaSingle` /
# `mediaInline` wrapper around a `media` node whose attrs identify the blob.
# _extract_adf_text drops those nodes entirely (it only walks `text`), so an
# inline screenshot was invisible to everything except the regex-based
# _count_image_refs, which cannot say WHICH attachment it is.
#
# WHY IT MATTERS DOWNSTREAM: an inline image is far likelier to be the mockup the
# requirements live in than a stray upload at the bottom of the ticket, so
# _ordered() -- in THIS module -- gives the ones marked `inline` the
# JIRA_MAX_IMAGES budget first. That ranking runs at the normalize_issue_payload
# call site, AFTER this join: the only order in which `inline` is known before
# the budget is spent.
#
# Ids here are Media-API identifiers and are NOT attachment ids -- the two id
# spaces are different, which is why the join back to `fields.attachment[]` is
# done on the media node's `alt` (Jira sets it to the uploaded FILENAME) rather
# than on the id. A media node that matches nothing is still reported; it just
# cannot be downloaded.
_MEDIA_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_:.-]{0,127}\Z")


_MAX_MEDIA_NODES = 50


_MAX_MEDIA_ALT_CHARS = 120


# Both come from UNTRUSTED host-submitted JSON and are stored, so they are
# length-capped at the point of extraction (the CONSUMER additionally
# syntax-gates the id before it may enter a URL path).
_MAX_ATTACHMENT_ID_CHARS = 128


_MAX_ATTACHMENT_URL_CHARS = 512


def _media_record(node: dict) -> dict | None:
    """{id, alt, media_type, collection} when *node* is an ADF media node, else None."""
    if str(node.get("type") or "") not in ("media", "mediaInline"):
        return None
    attrs = node.get("attrs")
    attrs = attrs if isinstance(attrs, dict) else {}
    media_id = str(attrs.get("id") or "").strip()
    if not _MEDIA_ID_RE.match(media_id):
        media_id = ""
    return {
        "id": media_id,
        "alt": str(attrs.get("alt") or "").strip()[:_MAX_MEDIA_ALT_CHARS],
        "media_type": str(attrs.get("type") or "").strip()[:32],
        "collection": str(attrs.get("collection") or "").strip()[:128],
    }


# Recursion is capped at _MAX_ADF_DEPTH exactly like _extract_adf_text, the
# node count at _MAX_MEDIA_NODES, and every echoed string is length-capped --
# this walks UNTRUSTED host-submitted JSON. Never raises: whatever was
# collected before a failure is returned.
def _extract_adf_media(node: object, depth: int = 0) -> list[dict]:
    """Every ADF media node under *node*: [{id, alt, media_type, collection}]."""
    out: list[dict] = []
    try:
        if depth > _MAX_ADF_DEPTH or not isinstance(node, dict):
            return out
        record = _media_record(node)
        if record is not None:
            out.append(record)
        content = node.get("content")
        if isinstance(content, list):
            for child in content:
                if len(out) >= _MAX_MEDIA_NODES:
                    break
                out.extend(_extract_adf_media(child, depth + 1))
        return out[:_MAX_MEDIA_NODES]
    except Exception:
        logger.exception("_extract_adf_media failed - keeping what was collected")
        return out[:_MAX_MEDIA_NODES]


def _add_comment_media(refs: list[dict], raw: object) -> None:
    """Append the media refs found in every COMMENT body of *raw* to *refs* (capped)."""
    if not isinstance(raw, list):
        return
    for comment in raw:
        if len(refs) >= _MAX_MEDIA_NODES:
            break
        if not isinstance(comment, dict):
            continue
        for ref in _extract_adf_media(comment.get("body")):
            ref["source"] = "comment"
            ref["comment_id"] = str(comment.get("id") or "")[:32]
            refs.append(ref)


# Comments matter as much as the description: a screenshot of the behaviour
# being described is nearly always pasted into the thread, not into the
# original ticket. Gated on JIRA_FETCH_IMAGES **and** JIRA_MAX_IMAGES > 0, the
# same pair _extract_image_attachments uses -- an install that asked for zero
# ticket images must not get inline ones by another door. Capped at
# _MAX_MEDIA_NODES overall. Never raises; makes no network request -- this is
# pure parsing of the payload already in hand.
def _media_enabled() -> bool:
    """True when settings ask for inline Jira images at all."""
    cfg = settings_port.get()
    return bool(cfg.jira_fetch_images) and cfg.jira_max_images > 0


def extract_media_refs(fields: object) -> list[dict]:
    """ADF media refs from the DESCRIPTION and from every COMMENT body."""
    try:
        if not _media_enabled() or not isinstance(fields, dict):
            return []
        refs: list[dict] = []
        for ref in _extract_adf_media(fields.get("description")):
            ref["source"] = "description"
            refs.append(ref)
        container = fields.get("comment")
        raw = container.get("comments") if isinstance(container, dict) else container
        _add_comment_media(refs, raw)
        return refs[:_MAX_MEDIA_NODES]
    except Exception:
        logger.exception("extract_media_refs failed - reporting no inline media")
        return []
