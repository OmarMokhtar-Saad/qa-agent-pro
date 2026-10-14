"""Host mode: duplicate-content and recent-suite advisories (C3c).

Moved from handlers/generate.py (v1.97.0 cursor-hardening, items 3a/8). Both
advisories are WARN-only: neither ever blocks generation or submit, and neither
raises. Every store call goes through the StoreHooks passed in, so patches on
the handler-side names still bite.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from agents.host_gen_hooks import StoreHooks
from agents.host_gen_notes import ago_label
from config.settings_guard import never_raise
from tools import clock
from tools.rag_store import RagQuery

logger = logging.getLogger("agents.host_mode")

_DUP_CORPUS_QUERY_CHARS_CAP = 4000
_DUP_CORPUS_TOP_K = 10
_RECENT_SUITE_WARN_WINDOW_S = 86400


def case_content_hash(tc: Any) -> str:
    """SHA-256 hex digest of the EXACT text gen_persist_suite writes to
    the corpus for this case, so the two always agree on byte-identical
    content. Never raises (returns "" on any failure)."""
    with never_raise(logger, "_case_content_hash failed", exc_info=True):
        steps_text = "\n".join(
            f"{s.step_number}. {s.action} -> {s.expected_result}" for s in tc.steps
        )
        content = f"{tc.title}\n{steps_text}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
    return ""


async def duplicate_case_note(cases: list, source_url: str, store: StoreHooks) -> tuple:
    """(note, counts) -- WARN-only hash check against the prior suite for this
    source (taken BEFORE it is overwritten) and ONE bounded corpus query.
    ("", zero counts) when nothing matches. Never raises."""
    counts = {"suite_id": "", "suite_n": 0, "corpus_n": 0, "m": 0}
    with never_raise(logger, "_duplicate_case_note failed", exc_info=True):
        hashes = {case_content_hash(tc) for tc in (cases or [])}
        hashes.discard("")
        counts["m"] = len(cases or [])
        if not hashes:
            return "", counts
        prior_suite_id, suite_n = await prior_suite_overlap(hashes, source_url, store)
        counts["suite_id"] = prior_suite_id
        counts["suite_n"] = suite_n
        counts["corpus_n"] = await corpus_overlap(cases, hashes, store)
        return _overlap_note(counts), counts
    return "", counts


def _overlap_note(counts: dict) -> str:
    """The warning line for the overlap *counts* ("" when there is none)."""
    m, suite_n, corpus_n = counts["m"], counts["suite_n"], counts["corpus_n"]
    if suite_n and counts["suite_id"]:
        return f"{suite_n}/{m} cases identical to suite `{counts['suite_id']}`"
    if corpus_n:
        return f"{corpus_n}/{m} cases match existing corpus entries"
    return ""


async def prior_suite_overlap(
    hashes: set, source_url: str, store: StoreHooks
) -> tuple[str, int]:
    """(prior suite id, how many of *hashes* it already holds) for *source_url*."""
    if not source_url:
        return "", 0
    recent = await store.find_recent_suite(source_url, window_s=None)
    prior = (recent.get("content") or {}) if not recent.get("error") else {}
    prior_suite_id = str(prior.get("suite_id") or "")
    if not prior_suite_id:
        return prior_suite_id, 0
    loaded = await store.load_suite(prior_suite_id)
    prior_suite = loaded.get("content")
    if prior_suite is None:
        return prior_suite_id, 0
    prior_hashes = {
        case_content_hash(tc) for tc in (getattr(prior_suite, "test_cases", None) or [])
    }
    return prior_suite_id, len(hashes & prior_hashes)


async def corpus_overlap(cases: list, hashes: set, store: StoreHooks) -> int:
    """How many of *hashes* one bounded corpus query returns; 0 on any failure."""
    with never_raise(logger, "_duplicate_case_note corpus check failed", exc_info=True):
        titles = " ".join(str(getattr(tc, "title", "") or "") for tc in (cases or []))
        titles = titles[:_DUP_CORPUS_QUERY_CHARS_CAP]
        if not titles.strip():
            return 0
        result = await store.query_corpus(
            titles, RagQuery(entry_type="test_case", top_k=_DUP_CORPUS_TOP_K)
        )
        hits = (result.get("content") or []) if not result.get("error") else []
        corpus_hashes = {
            hashlib.sha256(str(h.get("content") or "").encode("utf-8")).hexdigest()
            for h in hits
        }
        return len(hashes & corpus_hashes)
    return 0


async def recent_suite_warning_note(
    source_url: str, window_s: float, store: StoreHooks
) -> str:
    """\"> ...\" WARN when a suite already exists for source_url within
    window_s (item 8) -- restores what _find_recent_duplicate_suite gave up
    in 1.96.0. Never blocks generation. Never raises."""
    with never_raise(logger, "_recent_suite_warning_note failed", exc_info=True):
        if not source_url:
            return ""
        recent = await store.find_recent_suite(source_url, window_s=window_s)
        return _recent_warning(recent)
    return ""


def _recent_warning(recent: dict) -> str:
    """The "> ..." line for a find_recent_suite reply ("" when it holds no suite)."""
    if recent.get("error"):
        return ""
    row = recent.get("content")
    if not row:
        return ""
    suite_id = str(row.get("suite_id") or "")
    created_at = row.get("created_at")
    if not suite_id or not created_at:
        return ""
    age_s = max(0.0, clock.now() - float(created_at))
    age = ago_label(age_s)
    return (
        f"> ℹ️ Suite `{suite_id}` for this ticket was generated "
        f"{age} ago. Generation continues."
    )
