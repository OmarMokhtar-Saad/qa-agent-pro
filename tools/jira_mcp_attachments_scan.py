"""Jira MCP: attachments scan helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from tools import settings_port
from tools.jira_mcp_attachments import (
    _image_attachment_record,
    _match_media_to_attachments,
    _ordered,
)
from tools.jira_mcp_images import _image_ref_labels
from tools.jira_mcp_media import extract_media_refs

logger = logging.getLogger("tools.jira_mcp")

#: How many `fields.attachment` entries the image walk LOOKS AT, which is a
#: DIFFERENT axis from `jira_max_images` (how many it ultimately ships). They
#: were the same number until the inline-ordering fix, and conflating them is
#: exactly the defect: truncating on the delivery cap happened BEFORE
#: `_match_media_to_attachments` marked which images were inline, so a ticket
#: whose mockup was pasted into a comment lost it to whatever stray upload sat
#: at the top of the list. Ordering needs the WHOLE candidate set, so the
#: delivery cap moved to the call site and this bound took over the job the
#: break was really doing -- keeping a walk over untrusted host-submitted JSON
#: finite. The harmful direction is UPWARD: every entry admitted here is a dict
#: of length-capped strings held in memory before anything filters it.
_MAX_IMAGE_ATTACHMENTS_SCANNED = 50


def _scan_attachments(attachments: list) -> list[dict]:
    """Image records for the attachment entries, capped at the scan bound."""
    out: list[dict] = []
    for att in attachments:
        record = _image_attachment_record(att)
        if record is None:
            continue
        out.append(record)
        # NOT `jira_max_images` -- see _MAX_IMAGE_ATTACHMENTS_SCANNED.
        # The delivery cap is applied at the call site, AFTER the inline
        # join and the ordering it feeds; applying it here silently
        # discarded the candidates ordering exists to promote.
        if len(out) >= _MAX_IMAGE_ATTACHMENTS_SCANNED:
            break
    return out


# Image attachment METADATA from an issue payload.
#
# ``{filename, mime, size, id, content}``, for EVERY image candidate in the
# payload up to ``_MAX_IMAGE_ATTACHMENTS_SCANNED``. Deliberately NOT capped at
# ``jira_max_images``: that is the DELIVERY cap, and the caller applies it
# after ``_match_media_to_attachments`` has marked which images are inline and
# ``_ordered`` has ranked them. Capping here truncated candidates before they
# could be ranked, which is the defect the ranking exists to prevent.
# ``jira_fetch_images`` off, or a cap of zero, still yields nothing.
#
# THIS MODULE MAKES NO OUTBOUND HTTP REQUEST -- that hard rule is unchanged and
# unchangeable here. The credentialed attachment fetch that once consumed
# ``id``/``content`` is RETIRED (``docs/RETIRED_CAPABILITIES.md`` section 1):
# no module in this process downloads the bytes, so both fields are inert
# metadata echoed back from what the Atlassian MCP server already returned.
# They survive because the HOST -- the tester's own client, with the tester's
# own credential -- is what fetches images now.
#
# Both fields come from UNTRUSTED host-submitted JSON, so they are
# length-capped here, and any future CONSUMER -- not this function -- must
# syntax-gate the id before it may enter a URL path. Never raises.
def _extract_image_attachments(fields: dict) -> list[dict]:
    """Image attachment METADATA from an issue payload, up to the scan bound."""
    if (
        not settings_port.get().jira_fetch_images
        or settings_port.get().jira_max_images <= 0
    ):
        return []
    with never_raise(
        logger,
        "Extracting Jira image attachment metadata failed",
        level=logging.ERROR,
        exc_info=True,
    ):
        attachments = fields.get("attachment") if isinstance(fields, dict) else None
        if not isinstance(attachments, list):
            return []
        return _scan_attachments(attachments)
    return []


# Advisory (never a gate skip, never a refusal): the ticket's OWN
# description/media reference screens but zero attachments or embedded
# media arrived to back that up -- the 2026-09-25 tamper case deleted only
# `fields.attachment` and the "### UI Screen" section, so this cross-checks
# THREE independently-tamperable signals (the description's own
# "UI#nn"/heading labels via `_image_ref_labels`, ADF media nodes actually
# embedded via `_extract_adf_media`/`_match_media_to_attachments`, and the
# attachment list) rather than trusting any one of them alone. Silent when
# nothing references screens or when at least one attachment/media node
# backs the reference up. Never raises.
def _screen_reference_advisory(
    description_text: str, attachments: list, media_refs: list
) -> str:
    """Advisory: the ticket references screens but none arrived to back it up."""
    with never_raise(logger, "_screen_reference_advisory failed", exc_info=True):
        labels = _image_ref_labels(description_text or "")
        media_count = len([m for m in (media_refs or []) if m])
        attachment_count = len([a for a in (attachments or []) if a])
        referenced = max(len(labels), media_count)
        if referenced <= 0 or attachment_count > 0 or media_count > 0:
            return ""
        return f"ticket references {referenced} screen(s); {attachment_count} attachments staged"
    return ""


def _ranked_attachments(fields: dict) -> tuple[list[dict], list]:
    """``(attachments, media_refs)``: inline-first, then capped."""
    # ORDER MATTERS, and it is the whole point of this block. Extract every
    # candidate (bounded by _MAX_IMAGE_ATTACHMENTS_SCANNED), join the ADF
    # media nodes on so `inline` exists, rank inline-first, and only THEN
    # apply the delivery cap. Truncating before the join -- which is what
    # this did until the ordering fix -- dropped the mockup pasted into a
    # comment in favour of whatever stray upload sat at the top.
    attachments = _extract_image_attachments(fields)
    # Inline (pasted-into-the-body) images, from the description AND the
    # comment thread, joined onto the attachment records by filename.
    media_refs = extract_media_refs(fields)
    _match_media_to_attachments(media_refs, attachments)
    # `jira_max_images = 0` is a legitimate "allow none" cap and still
    # yields nothing, because the slice runs after the ranking, not instead
    # of it.
    return _ordered(attachments)[
        : max(0, settings_port.get().jira_max_images)
    ], media_refs
