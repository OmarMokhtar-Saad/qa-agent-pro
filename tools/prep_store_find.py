"""Recent-prep and Jira-snapshot lookups by source (leaf of ``tools.prep_store``)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Iterator
from typing import Any

from tools.prep_store_conn import _connect, is_finalized_record

logger = logging.getLogger("tools.prep_store")


def _newest_rows() -> list:
    conn = _connect()
    try:
        return conn.execute(
            "SELECT id, payload_json, created_at FROM preps "
            "ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
    finally:
        conn.close()


def _recent_envelopes(window_s: float, now: float) -> Iterator[tuple[str, float, dict]]:
    for pid, payload_json, created in _newest_rows():
        try:
            created_f = float(created)
        except (TypeError, ValueError, OverflowError):
            continue
        if window_s and (now - created_f) > window_s:
            break  # ordered DESC, so everything older is out of the window too
        try:
            env = json.loads(payload_json) or {}
        except (ValueError, TypeError):
            continue
        yield pid, created_f, env


def _clamped_count(value: Any) -> int:
    """``value`` as an int clamped to 0..99; anything unreadable is 0."""
    try:
        return max(0, min(99, int(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _clean_strings(raw: object, limit: int, item_len: int | None = None) -> list[str]:
    """Non-blank stripped strings of a list/tuple (a stray STRING is ignored,
    it would otherwise iterate into a list of characters), at most ``limit``."""
    items = raw if isinstance(raw, (list, tuple)) else []
    return [str(x or "").strip()[:item_len] for x in items if str(x or "").strip()][
        :limit
    ]


def _image_state(meta: dict) -> dict:
    """The IMAGE state of an open prep (2026-08-09), so handlers/
    can carry its screens forward into a re-prepare -- or refuse and NAME them
    -- instead of silently generating ungrounded. IDS AND COUNTS ONLY: image
    bytes are never persisted here. Every field is coerced defensively: this
    lookup is a best-effort guard, a malformed meta degrades to "no images",
    never raises."""
    return {
        "captured_image_count": _clamped_count(meta.get("captured_image_count")),
        "attached_image_count": _clamped_count(meta.get("attached_image_count")),
        "capture_ids": _clean_strings(meta.get("capture_ids"), 24, 64),
        "captured_image_labels": _clean_strings(meta.get("captured_image_labels"), 8),
        "host_image_job": bool(meta.get("host_image_job")),
    }


def _find_recent_prep_sync(source_url: str, window_s: float) -> dict | None:
    now = time.time()
    for pid, created_f, env in _recent_envelopes(window_s, now):
        if is_finalized_record(env):
            continue  # finished work is the SUITE guard's business, not this one
        meta = env.get("meta") or {}
        if str(meta.get("source_url") or "") != source_url:
            continue
        return {
            "prep_id": pid,
            "created_at": created_f,
            "age_s": max(0.0, now - created_f),
            **_image_state(meta),
        }
    return None


def _snapshot_chars(meta: dict) -> int:
    try:
        # F9 (2026-08-15): how much ticket text the SAME revision yielded
        # last time. 0 for an envelope written before this key existed,
        # which the caller reads as "no baseline" and stays silent about.
        return int(meta.get("jira_content_chars") or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _find_prep_snapshot_sync(source_url: str, window_s: float) -> dict | None:
    now = time.time()
    for pid, created_f, env in _recent_envelopes(window_s, now):
        meta = env.get("meta") or {}
        if str(meta.get("source_url") or "") != source_url:
            continue
        stamp = str(meta.get("jira_updated") or "").strip()[:64]
        if not stamp:
            continue  # a prep written before the stamp existed proves nothing
        return {
            "prep_id": pid,
            "created_at": created_f,
            "age_s": max(0.0, now - created_f),
            "jira_updated": stamp,
            "content_chars": max(0, _snapshot_chars(meta)),
        }
    return None


# 2026-08-03. The duplicate-prep guard was named for preps but only ever looked
# at finished SUITES (``_find_recent_duplicate_suite``). So the exact case it
# exists for -- two `qa_prepare_test_cases` calls in a row for one source, before
# any suite exists -- was invisible to it. A real run made two preps 43 seconds
# apart with byte-identical source_url and source_text, got no warning, and threw
# away a full preparation.
#
# Deliberately keyed on exact ``meta.source_url`` (same rule the suite guard
# uses), so free-text descriptions -- which have no stable identity -- are never
# flagged. FINALIZED preps are skipped: those are a completed generation and
# belong to the suite guard, which can report the suite id and case count.
#
# Scans at most the 20 newest preps and stops at the first one outside the
# window, so cost does not grow with history. Never raises.
#
# 2026-08-09: the hit also carries that prep's IMAGE state --
# ``captured_image_count`` / ``attached_image_count`` / ``capture_ids`` /
# ``captured_image_labels`` / ``host_image_job`` -- so the caller can carry
# device screens forward into a re-prepare, or refuse and name what would
# otherwise vanish. Ids and counts only: no image bytes are stored here, and
# an OLD envelope that predates those keys simply reports zeros and empties.
async def find_recent_prep_by_source(source_url: str, window_s: float = 1800) -> dict:
    """A recent, still-unfinalized prep for the SAME source_url, or None content."""
    try:
        url = str(source_url or "").strip()
        if not url:
            return {"error": None, "content": None}
        hit = await asyncio.to_thread(
            _find_recent_prep_sync, url, float(max(0, window_s))
        )
        return {"error": None, "content": hit}
    except Exception as exc:
        logger.exception("prep_store.find_recent_prep_by_source failed")
        return {"error": str(exc), "content": None}


# 2026-08-10 (I2c). Deliberately NOT `find_recent_prep_by_source`, for two
# reasons: that lookup SKIPS finalized preps -- and a finished generation is
# exactly the snapshot a later call has to be compared against -- and it is
# keyed to the duplicate-prep guard's window, whereas this is called with
# QA_PREP_TTL_S so the staleness check is independent of that guard's flag.
#
# Preps with no `jira_updated` stamp are skipped rather than returned empty,
# so an envelope written before the key existed can never be mistaken for a
# fresher snapshot. Same bounded 20-row scan as the guard; ids and one short
# timestamp only. Never raises.
async def find_prep_snapshot_by_source(
    source_url: str, window_s: float = 86400
) -> dict:
    """The newest prep for *source_url* that stamped a Jira ``fields.updated``."""
    try:
        url = str(source_url or "").strip()
        if not url:
            return {"error": None, "content": None}
        hit = await asyncio.to_thread(
            _find_prep_snapshot_sync, url, float(max(0, window_s))
        )
        return {"error": None, "content": hit}
    except Exception as exc:
        logger.exception("prep_store.find_prep_snapshot_by_source failed")
        return {"error": str(exc), "content": None}
