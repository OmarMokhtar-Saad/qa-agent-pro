"""Jira MCP: result blocks of normalize_issue_payload (split from tools/jira_mcp.py)."""

from __future__ import annotations

from typing import NamedTuple

from tools import settings_port
from tools.jira_mcp_adf import adf_text_with_depth_flag
from tools.jira_mcp_attachments_scan import (
    _ranked_attachments,
    _screen_reference_advisory,
)
from tools.jira_mcp_errors import _sanitize_echo
from tools.jira_mcp_images import _count_image_refs, _image_ref_labels
from tools.jira_mcp_issue_key import IssueRef
from tools.jira_mcp_parent import _parent_grounding


class SourceText(NamedTuple):
    """The SOURCE text fields plus the truncation flags."""

    summary: str
    description: str
    description_truncated: bool
    ac: str
    any_truncated: bool


# Emptiness is judged on these SOURCE fields, BEFORE any fallback value is
# applied to `title`. Judging it afterwards (on `title` / `raw_text`) would
# let a ticket whose summary AND description are both empty look "grounded"
# purely because the issue KEY was substituted for the title, and a whole
# suite would then be generated from nothing.
def _source_text(fields: dict) -> SourceText:
    """``(summary, description, description_truncated, ac, any_truncated)``."""
    summary_src = str(fields.get("summary") or "").strip()
    description_raw, description_truncated = adf_text_with_depth_flag(
        fields.get("description")
    )
    # The AC field is ADF too, so the same cap can empty it silently -- the
    # very defect this fix exists to close, one field over.
    ac_raw, ac_truncated = adf_text_with_depth_flag(
        fields.get(settings_port.get().jira_ac_field)
    )
    # `description_truncated` keeps its literal meaning; `any_truncated`
    # drives the disclosure, because an unread AC field is just as silent.
    return SourceText(
        summary_src,
        description_raw.strip(),
        description_truncated,
        ac_raw.strip(),
        description_truncated or ac_truncated,
    )


# Additive key (2026-09-02): `issue_key_from_url` is True when the payload
# carried no `key` and the URL's key was assumed. Read by _grounding_source_note.
# Additive key (2026-08-10): `updated` is the issue's own last-modified stamp,
# echoed back so the server can DISCLOSE which snapshot a generation ran on.
# UNTRUSTED like every other echoed field: one line, URL-free, backtick-free,
# capped. A host that trimmed `fields` yields "".
def _provenance_block(fields: dict, ref: IssueRef, raw_text: str) -> dict:
    """Where the result came from: the key, its source, the snapshot stamp."""
    return {
        "content": raw_text,
        "issue_key": ref.key,
        "issue_key_from_url": ref.assumed_from_url,
        "updated": _sanitize_echo(fields.get("updated"), 40),
        "source": "atlassian_mcp",
        "error": None,
    }


# Independent of `attachment`, so a host that trims the issue JSON cannot
# silence them. `description_truncated` is True when _MAX_ADF_DEPTH stopped
# the description walk, so a caller can tell a short ticket from an unread
# one (B1, 2026-09-01).
def _description_image_block(description: str, description_truncated: bool) -> dict:
    """The disclosures about images embedded IN the description."""
    return {
        "description_truncated": description_truncated,
        "description_image_refs": _count_image_refs(description),
        "description_image_labels": _image_ref_labels(description),
    }


# "The ticket has no images" and "nobody requested the attachment field" are
# different facts, and only the first is safe to stay quiet about: a host may
# have trimmed `attachment` out of `fields`.
def _attachments_unknown(fields: dict) -> bool:
    """True when the host may have trimmed the ``attachment`` field away."""
    return bool(
        settings_port.get().jira_fetch_images
        and isinstance(fields, dict)
        and "attachment" not in fields
    )


# The Atlassian MCP server returns attachment METADATA, not bytes, and this
# module makes no outbound HTTP requests - so ticket screenshots cannot ride
# along any more (`images` stays empty; `images_unavailable` reports it).
# `media_refs` (additive, 2026-08-09) are the ADF media nodes found in the
# description and the comment bodies. v1.97.0 cursor-hardening (item 7): the
# advisory cross-checks THREE independently-tamperable signals (description
# labels, embedded media, attachments) so deleting only `fields.attachment`
# no longer erases the evidence trail.
def _image_block(fields: dict, description: str, description_truncated: bool) -> dict:
    """Every image-related key: attachments, embedded refs and the disclosures."""
    attachments, media_refs = _ranked_attachments(fields)
    return {
        "images": [],
        "image_attachments": attachments,
        "images_unavailable": bool(attachments),
        "media_refs": media_refs,
        "image_reference_advisory": _screen_reference_advisory(
            description, attachments, media_refs
        ),
        "attachments_unknown": _attachments_unknown(fields),
        **_description_image_block(description, description_truncated),
    }


# `sibling_stories` (additive, 2026-08-03) is WHICH sibling stories were
# folded into parent_context.
def _context_block(fields: dict, payload: dict, key: str) -> dict:
    """Parent, subtasks, links, sibling stories and the folded parent context."""
    parent, subtasks, issuelinks, siblings, parent_context = _parent_grounding(
        fields, payload, key
    )
    return {
        "parent": parent,
        "subtasks": subtasks,
        "issuelinks": issuelinks,
        "sibling_stories": siblings,
        "parent_context": parent_context,
    }
