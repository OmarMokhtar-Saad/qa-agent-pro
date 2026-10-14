"""RAG Store — lightweight TF-IDF/Jaccard corpus for test cases and bug reports.

Contract (never-raises):
  On success: {"error": None, "content": <value>}
  On failure: {"error": str, "content": None}

Storage: JSONL under settings.qa_rag_storage_path; disk I/O in asyncio.to_thread().

Facade: owns every settings read and the public API; the pure helpers live
in the ``tools.rag_store_*`` leaves and are re-exported.
"""

from __future__ import annotations

import asyncio
import json  # noqa: F401
import logging
import os  # noqa: F401
import re  # noqa: F401
import threading  # noqa: F401
import time  # noqa: F401
import uuid  # noqa: F401
from pathlib import Path
from typing import Any

from config.settings_guard import never_raise
from tools import settings_port
from tools.install_paths import resolve_data_path
from tools.rag_store_disk import (  # noqa: F401
    _CORPUS_CACHE,
    _CORPUS_CACHE_LOCK,
    _SAFE_ENTRY_TYPE,
    _corpus_file,
    _count_entries_sync,
    _file_signature,
    _load_corpus_sync,
    _new_entry,
    _save_entry_sync,
    load_corpus_sync,
)
from tools.rag_store_prune import _prune_sync as prune_sync  # noqa: F401
from tools.rag_store_rank import (  # noqa: F401
    RagQuery,
    RankOpts,
    _apply_recency,
    _log_query,
    _rank_entries,
    _score_entries,
)
from tools.rag_store_score import _bm25_scores, _cosine_tfidf_scores  # noqa: F401
from tools.rag_store_src import (  # noqa: F401
    _delete_source_sync,
    _is_superseded,
    _last_keep_index,
    _rewrite_without_source,
)
from tools.rag_store_tok import (  # noqa: F401
    _jaccard_similarity,
    _tokenize,
    _tokenize_list,
)

logger = logging.getLogger(__name__)


# Map an entry_type to its JSONL corpus file, fail-closed (see _corpus_file).
# Install-root anchored (tools/install_paths): a cwd-relative corpus split the
# history and one copy hit the entry cap, where every write rewrites the file.
def _corpus_path(entry_type: str) -> Path:
    base = resolve_data_path(settings_port.get().qa_rag_storage_path)
    return _corpus_file(base, entry_type)


# Roughly how many entries this corpus holds. 0 on any error.
#
# Reuses the newline scan the prune gate already uses -- no JSON parsing, so
# it stays cheap on a 5,000-entry corpus. Added 2026-09-01 so qa-doctor can
# say what it OBSERVED ("grounding on 459 past cases") instead of printing a
# green tick for a feature that is a silent no-op on an empty corpus.
async def corpus_entry_count(entry_type: str = "test_case") -> int:
    with never_raise(logger, "corpus_entry_count failed", exc_info=True):
        return await asyncio.to_thread(_count_entries_sync, _corpus_path(entry_type))
    return 0


# Strict type check: pydantic guarantees a real int in production;
# anything else (e.g. a fully-mocked settings) means "no cap".
def _max_entries() -> int:
    cap_raw = getattr(settings_port.get(), "qa_rag_max_entries", 0)
    return cap_raw if isinstance(cap_raw, int) else 0


# DEMOTED from INFO (2026-07-30 run review, item 8): one line PER corpus entry
# meant 65 lines on a single submit, and because all logging goes to stderr
# Cursor renders every one of them as `[error]`, burying real errors. The
# aggregate "RAG: persisted N test case(s) to corpus" line in
# handlers.gen_persist_suite carries the operational fact.
# Append a new entry to the corpus.
# Returns {"error": None, "content": {"id": str}} on success.
# Returns {"error": str, "content": None} on any exception.
# Never raises.
async def add_to_corpus(entry_type: str, content: str, metadata: dict) -> dict:
    """Append a new entry to the corpus. Never raises."""
    with never_raise(
        logger, "rag_store.add_to_corpus failed", level=logging.ERROR, exc_info=True
    ) as g:
        entry = _new_entry(entry_type, content, metadata)
        path = _corpus_path(entry_type)
        await asyncio.to_thread(_save_entry_sync, path, entry)
        cap = _max_entries()
        if cap > 0:
            await asyncio.to_thread(prune_sync, path, cap)
        logger.debug(
            "rag_store: added %s entry %s to %s", entry_type, entry["id"], path
        )
        return {"error": None, "content": {"id": entry["id"]}}
    return {"error": str(g.exc), "content": None}


def _log_removed(removed: int, path: Path) -> None:
    if removed:
        logger.info(
            "rag_store: removed %d superseded entry(ies) for one source in %s",
            removed,
            path.name,
        )


# Remove a source's SUPERSEDED entries so a re-run replaces, not appends.
async def replace_source_entries(
    entry_type: str, source_key: str, *, keep_ids: Any = None
) -> dict:
    with never_raise(
        logger,
        "rag_store.replace_source_entries failed",
        level=logging.ERROR,
        exc_info=True,
    ) as g:
        key = str(source_key or "").strip()
        if not key:
            return {"error": None, "content": {"removed": 0}}
        keep = {str(i) for i in (keep_ids or []) if str(i or "")}
        path = _corpus_path(entry_type)
        removed = await asyncio.to_thread(_delete_source_sync, path, key, keep)
        _log_removed(removed, path)
        return {"error": None, "content": {"removed": removed}}
    return {"error": str(g.exc), "content": None}


def _similarity_mode() -> str:
    mode = getattr(settings_port.get(), "qa_rag_similarity_mode", "jaccard")
    mode = (mode or "jaccard").lower()
    if mode not in ("jaccard", "cosine", "bm25"):
        logger.warning(
            "Unknown QA_RAG_SIMILARITY_MODE=%r — falling back to 'jaccard'",
            mode,
        )
        mode = "jaccard"
    return mode


def _recency_half_life() -> int:
    hl_raw = getattr(settings_port.get(), "qa_rag_recency_half_life_days", 0)
    return hl_raw if isinstance(hl_raw, int) else 0


def _rank_opts(query_text: str, query_tokens: set, q: RagQuery) -> RankOpts:
    mode, half_life = _similarity_mode(), _recency_half_life()
    return RankOpts(
        query_text, query_tokens, q.metadata_filter, q.top_k, mode, half_life
    )


async def _load_all(entry_type: str | None) -> list[dict]:
    if entry_type is not None:
        paths = [_corpus_path(entry_type)]
    else:
        paths = [_corpus_path("test_case"), _corpus_path("bug_report")]
    all_entries: list[dict] = []
    for path in paths:
        all_entries.extend(await asyncio.to_thread(load_corpus_sync, path))
    return all_entries


async def query_corpus(query_text: str, q: RagQuery | None = None) -> dict:
    """Top-k corpus entries most similar to query_text; never raises."""
    with never_raise(
        logger, "rag_store.query_corpus failed", level=logging.ERROR, exc_info=True
    ) as g:
        query_tokens = _tokenize(query_text or "")
        if not query_text or not query_text.strip() or not query_tokens:
            return {"error": None, "content": []}
        q = q or RagQuery()
        all_entries = await _load_all(q.entry_type)
        if not all_entries:
            return {"error": None, "content": []}
        results = _rank_entries(all_entries, _rank_opts(query_text, query_tokens, q))
        _log_query(results, len(all_entries))
        return {"error": None, "content": results}
    return {"error": str(g.exc), "content": None}
