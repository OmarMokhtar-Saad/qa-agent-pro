"""Jira MCP: comments helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import logging

from tools import settings_port
from tools.jira_mcp_adf import _as_text

logger = logging.getLogger("tools.jira_mcp")


def _comment_lines(records: list[dict]) -> list[str]:
    """Render comment records as the legacy "Author: body" strings.

    The public ``comments`` key has always had this shape, so keeping it
    byte-identical means every existing consumer and test is unaffected by the
    richer ``comments_meta`` records. Never raises.
    """
    try:
        out: list[str] = []
        for rec in records or []:
            body = str(rec.get("body") or "").strip()
            if body:
                out.append(f"{rec.get('author') or 'Unknown'}: {body}")
        return out
    except Exception:
        logger.exception("Rendering Jira comment lines failed")
        return []


def _effective_comment_cap() -> int:
    """How many comments to keep from the host-supplied payload.

    Returns ``jira_max_comments`` (default 5), and nothing else. Until
    2026-08-15 this widened to
    ``max(jira_max_comments, qa_comment_reconcile_max_comments)`` inside an
    ``if comment_reconciler.enabled():`` branch -- unreachable since that seam
    was pinned False on 2026-08-14, and deleted outright by dead-code deletion
    batch D5 together with the module and the setting. The function itself is
    RETAINED: it is the single place the comment window is decided, and
    _extract_comment_records is written against it. Never raises.
    """
    try:
        return int(settings_port.get().jira_max_comments)
    except (TypeError, ValueError, OverflowError):
        return 5


def _comment_record(c: object) -> dict | None:
    """{id, author, created, body} for one comment entry, or None when it has no body."""
    if not isinstance(c, dict):
        return None
    body = _as_text(c.get("body")).strip()
    if not body:
        return None
    author = c.get("author")
    author = author if isinstance(author, dict) else {}
    return {
        "id": str(c.get("id") or ""),
        "author": str(author.get("displayName") or "Unknown"),
        "created": str(c.get("created") or ""),
        "body": body,
    }


def _raw_comments(fields: dict) -> object:
    """The raw ``fields.comment`` list (unwrapping the ``comments`` container), or None."""
    container = fields.get("comment") if isinstance(fields, dict) else None
    if isinstance(container, dict):
        return container.get("comments")
    return container


# Comment RECORDS from an issue payload, OLDEST FIRST, capped.
#
# The Atlassian MCP ``getJiraIssue`` response embeds the thread at
# ``fields.comment.comments`` in ASCENDING (oldest-first) order. That is the
# order every downstream consumer wants, but a long thread must keep the
# NEWEST N (those carry the current requirements), so the TAIL is taken and
# the chronological order is preserved. Never raises; returns [] when
# JIRA_FETCH_COMMENTS is off or the cap is 0.
def _extract_comment_records(fields: dict) -> list[dict]:
    """Comment RECORDS, OLDEST FIRST, newest ``cap`` kept. Never raises; [] when off."""
    cap = _effective_comment_cap()
    if not settings_port.get().jira_fetch_comments or cap <= 0:
        return []
    try:
        raw = _raw_comments(fields)
        if not isinstance(raw, list):
            return []
        records = (_comment_record(c) for c in raw)
        out = [r for r in records if r is not None]
        # Keep the NEWEST cap records but hand them back chronologically.
        return out[-cap:]
    except Exception:
        logger.exception("Extracting Jira comment records failed")
        return []
