"""Jira MCP: attachments helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import logging
from typing import Any

from tools.jira_mcp_media import _MAX_ATTACHMENT_ID_CHARS, _MAX_ATTACHMENT_URL_CHARS

logger = logging.getLogger("tools.jira_mcp")


def _attachment_index(attachments: Any) -> dict:
    """Attachment records keyed by lower-cased filename and by its extension-less stem."""
    by_name: dict = {}
    for att in attachments or []:
        if not isinstance(att, dict):
            continue
        name = str(att.get("filename") or "").strip().lower()
        if not name:
            continue
        by_name.setdefault(name, att)
        by_name.setdefault(name.rsplit(".", 1)[0], att)
    return by_name


def _join_media_ref(ref: object, by_name: dict) -> None:
    """Mark the attachment a media ref's alt names as inline, and tag the ref."""
    if not isinstance(ref, dict):
        return
    alt = str(ref.get("alt") or "").strip().lower()
    if not alt:
        return
    att = by_name.get(alt) or by_name.get(alt.rsplit(".", 1)[0])
    if att is None:
        return
    att["inline"] = True
    if ref.get("id"):
        att.setdefault("media_id", ref["id"])
    ref["attachment_filename"] = att.get("filename")


# Join inline media nodes onto attachment records IN PLACE.
#
# Matched attachments gain ``inline: True`` (and the media id, for diagnosis);
# matched refs gain ``attachment_filename``. The join is on the media node's ``alt`` against the
# attachment ``filename`` -- exact, case-insensitive, with a stem fallback for
# the case where Jira drops the extension from alt. An unmatched ref changes
# nothing at all, which is the safe direction: a wrong join would credit a
# screenshot the ticket does not have, and ``inline`` decides which images win
# the download budget. Never raises.
def _match_media_to_attachments(media: Any, attachments: Any) -> None:
    """Join inline media nodes onto attachment records IN PLACE. Never raises."""
    try:
        by_name = _attachment_index(attachments)
        for ref in media or []:
            _join_media_ref(ref, by_name)
    except Exception:
        logger.exception("_match_media_to_attachments failed - leaving both lists")


def _ordered(attachments: list) -> list:
    """INLINE images first, ticket order otherwise.

    ``JIRA_MAX_IMAGES`` truncates, and an image pasted INTO the description or a
    comment is far likelier to be the mockup the requirements live in than a
    stray upload at the bottom of the ticket -- so the inline ones, which
    ``_match_media_to_attachments`` marks from the ADF media nodes, win the
    budget. ``sorted`` is stable, so everything else keeps ticket order.

    Recovered from the deleted ``tools/jira_attachments.py`` (``git show
    cdc6fcc:tools/jira_attachments.py``), which is the only place this ranking
    ever lived; reviving the ranking does NOT revive that module or its
    credentialed fetch. Never raises.
    """
    try:
        return sorted(attachments, key=lambda a: 0 if a.get("inline") else 1)
    except (AttributeError, TypeError):
        logger.debug("_ordered failed - keeping ticket order", exc_info=True)
        return list(attachments)


def _image_attachment_record(att: object) -> dict | None:
    """One attachment's metadata record, or None when it is not an image."""
    if not isinstance(att, dict):
        return None
    mime = str(att.get("mimeType") or "").lower()
    if not mime.startswith("image/"):
        return None
    return {
        "filename": str(att.get("filename") or "attachment"),
        "mime": mime,
        "size": att.get("size") or 0,
        # Metadata only -- see _extract_image_attachments. Nothing in this
        # process reads them: the credentialed fetch that used to is
        # retired. Length-capped because they are untrusted stored strings.
        "id": str(att.get("id") or "")[:_MAX_ATTACHMENT_ID_CHARS],
        "content": str(att.get("content") or "")[:_MAX_ATTACHMENT_URL_CHARS],
    }
