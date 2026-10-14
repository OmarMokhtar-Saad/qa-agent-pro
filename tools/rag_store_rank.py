"""Filter, score, recency-boost and rank RAG corpus entries (pure; the facade
reads settings and passes ``mode`` / ``half_life`` in)."""

from __future__ import annotations

import calendar
import logging
import math
import time
from dataclasses import dataclass

from tools.rag_store_score import _bm25_scores, _cosine_tfidf_scores
from tools.rag_store_tok import _jaccard_similarity, _tokenize, _tokenize_list

logger = logging.getLogger("tools.rag_store")


@dataclass(frozen=True)
class RagQuery:
    """Optional narrowing for ``query_corpus``."""

    entry_type: str | None = None
    top_k: int = 5
    metadata_filter: dict | None = None


@dataclass(frozen=True)
class RankOpts:
    """Everything ``_rank_entries`` needs besides the entries themselves."""

    query_text: str
    query_tokens: set
    metadata_filter: dict | None
    top_k: int
    mode: str
    half_life: int


def _score_entries(usable: list[dict], ro: RankOpts) -> list:
    """Pair every usable entry with its similarity score in the configured mode."""
    if ro.mode == "bm25":
        docs_tokens = [_tokenize_list(e["content"]) for e in usable]
        bm25 = _bm25_scores(_tokenize_list(ro.query_text), docs_tokens)
        return list(zip(bm25, usable))
    if ro.mode == "cosine":
        # TF-IDF cosine gives rare/discriminative terms more weight than
        # Jaccard's flat set overlap — better "we already have this" dedup.
        docs_tokens = [_tokenize_list(e["content"]) for e in usable]
        cos_scores = _cosine_tfidf_scores(_tokenize_list(ro.query_text), docs_tokens)
        return list(zip(cos_scores, usable))
    return [
        (_jaccard_similarity(ro.query_tokens, _tokenize(e["content"])), e)
        for e in usable
    ]


def _age_days(entry: dict, now: float) -> float:
    raw = entry.get("added_at") or ""
    try:
        ts = calendar.timegm(time.strptime(raw, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, OverflowError):
        return float("inf")
    return max(0.0, (now - ts) / 86400.0)


# Boost newer entries when a recency half-life is configured. Freshness boost
# (research-backed): newer entries get up to +15%, decaying exponentially with
# the configured half-life. Capped at 1.0 so score bounds hold in every mode.
def _apply_recency(scored: list, half_life: int) -> list:
    if half_life <= 0:
        return scored
    now = time.time()
    return [
        (min(1.0, s * (1.0 + 0.15 * math.exp(-_age_days(e, now) / half_life))), e)
        for s, e in scored
    ]


def _md_match(entry: dict, metadata_filter: dict) -> bool:
    md = entry.get("metadata") or {}
    return all(
        str(v).lower() in str(md.get(k, "")).lower()
        for k, v in metadata_filter.items()
        if v
    )


def _filter_entries(all_entries: list[dict], metadata_filter: dict | None) -> list:
    usable = [e for e in all_entries if (e.get("content") or "")]
    if metadata_filter:
        # Case-insensitive substring match on each requested metadata key —
        # narrowing before scoring is the highest-value dedup lever.
        usable = [e for e in usable if _md_match(e, metadata_filter)]
    return usable


def _top_results(scored: list, top_k: int) -> list[dict]:
    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {
            "content": entry.get("content", ""),
            "metadata": entry.get("metadata", {}),
            "score": round(score, 4),
        }
        for score, entry in scored[:top_k]
        if score > 0
    ]


def _rank_entries(all_entries: list[dict], ro: RankOpts) -> list[dict]:
    """Filter, score, recency-boost and cut the entries to the top-k results."""
    usable = _filter_entries(all_entries, ro.metadata_filter)
    scored = _apply_recency(_score_entries(usable, ro), ro.half_life)
    return _top_results(scored, ro.top_k)


# Find the top-k corpus entries most similar to query_text.
#
# Similarity is Jaccard overlap on whitespace+punctuation tokens.
#
# Returns {"error": None, "content": list[{"content": str, "metadata": dict, "score": float}]}
# on success (list may be empty if corpus is empty or query produces no tokens).
# Returns {"error": str, "content": None} on any exception.
# Never raises.
# (the query_corpus facade in tools/rag_store.py implements this.)
def _log_query(results: list[dict], total: int) -> None:
    logger.info(
        "rag_store.query_corpus: query returned %d/%d results (top score %.4f)",
        len(results),
        total,
        results[0]["score"] if results else 0.0,
    )
