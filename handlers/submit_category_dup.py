"""The duplicate notes of a category reply: the live check and the shortlist."""

from __future__ import annotations

import logging
import sys

from agents.host_dedup import (
    build_dup_shortlist,
    build_dup_shortlist_section,
    dup_shortlist_on,
)
from config.settings_guard import never_raise
from handlers import generate, prep_status
from handlers.seam_alias import alias
from handlers.submit_category_view import _raw_names, _status_view

logger = logging.getLogger("tools.mcp_handlers")


async def _category_dup_note(parsed, meta: dict) -> str:
    """Best-effort duplicate check at category-submit time, as a reply block."""
    # v1.97.0 cursor-hardening (item 3a): no suite object exists yet here, so
    # the note folds straight into the reply string; the Generation Notes
    # sheet only ever sees the finalize-time (handle_submit_suite) signal.
    # VERIFIED 2026-09-26: prepare writes both keys into the envelope
    # meta ("source_text" / "source_url"); read defensively anyway so a
    # pre-1.97 envelope degrades to "" instead of raising.
    with never_raise(logger, "category duplicate check failed", exc_info=True):
        source = str(meta.get("source_url") or meta.get("source_text") or "")
        dup_note, _ = await generate.gen_duplicate_case_note(
            getattr(getattr(parsed, "suite", None), "test_cases", None) or [],
            source,
        )
        if dup_note:
            return f"\n\n> ℹ️ {dup_note}"
    return ""


def _shortlist_if_ready(expected: list, rows: list) -> str:
    """The shortlist section once every expected category is staged, else ""."""
    if not _status_view(expected, _raw_names(rows)).get("ready"):
        return ""
    merged_dict, _used, _id_map = prep_status.merge_category_rows(rows)
    pairs = build_dup_shortlist(merged_dict.get("test_cases") or [])
    return build_dup_shortlist_section(pairs)


# Server-assisted duplicate shortlist (QA_DUP_SHORTLIST_ENABLED, OFF).
#
# When THIS submission completed the expected category set, merge the
# staged rows (pure -- nothing is persisted or deleted) and append
# lexically prescreened candidate duplicate pairs, printed with POST-MERGE
# GLOBAL tc_ids so the host confirms a shortlist via the finalize sidecar
# instead of re-reading the merged suite. Needs
# QA_HOST_DEDUP_REVIEW_ENABLED (it feeds that review). Returns "" when
# gated off, not ready, no pairs, or on ANY error -- it must never break or
# block a category submit. Deliberate F4/F11 carve-out, stated here so it
# reads as a decision: this section names `duplicate_groups` in a
# qa_submit_category reply, but only under the OFF-by-default flag and only
# as the server's OWN prescreen output (a review the server just ran), not
# as a request to attach the field to a per-category submission.
async def dup_shortlist_note(meta: object, rows_content: list) -> str:
    """Server-assisted duplicate shortlist (QA_DUP_SHORTLIST_ENABLED, OFF)."""
    with never_raise(logger, "dup shortlist prescreen failed", exc_info=True):
        if not dup_shortlist_on() or not isinstance(meta, dict):
            return ""
        expected = list(meta.get("expected_categories") or [])
        if not expected:
            return ""
        return _shortlist_if_ready(expected, rows_content)
    return ""


_dup_shortlist_note = alias(sys.modules[__name__], "dup_shortlist_note")
