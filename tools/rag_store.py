"""RAG Store — lightweight TF-IDF/Jaccard corpus for test cases and bug reports.

Contract (never-raises):
  On success: {"error": None, "content": <value>}
  On failure: {"error": str, "content": None}

Storage: JSONL files under settings.qa_rag_storage_path.
All blocking disk I/O is wrapped in asyncio.to_thread().
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> set[str]:
    """Split text on whitespace and punctuation, lowercase all tokens."""
    return set(re.split(r"[\W_]+", text.lower())) - {""}


def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Return |A∩B| / |A∪B|, or 0.0 when both sets are empty."""
    if not set_a and not set_b:
        return 0.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def _tokenize_list(text: str) -> list[str]:
    """Like _tokenize but keeps token counts (for TF-IDF)."""
    return [t for t in re.split(r"[\W_]+", text.lower()) if t]


def _cosine_tfidf_scores(
    query_tokens: list[str], docs_tokens: list[list[str]]
) -> list[float]:
    """Cosine similarity between a query and each doc under a TF-IDF weighting
    computed over the docs + query (opt-in dedup mode, I-051). Pure Python, no
    external model. Returns one score in [0, 1] per doc. Never raises."""
    import math
    from collections import Counter

    try:
        corpus = docs_tokens + [query_tokens]
        n = len(corpus)
        df: Counter = Counter()
        for toks in corpus:
            for t in set(toks):
                df[t] += 1

        def _idf(t: str) -> float:
            return math.log((n + 1) / (df.get(t, 0) + 1)) + 1.0

        def _vec(toks: list[str]) -> dict:
            if not toks:
                return {}
            tf = Counter(toks)
            total = len(toks)
            return {t: (c / total) * _idf(t) for t, c in tf.items()}

        qv = _vec(query_tokens)
        qnorm = math.sqrt(sum(v * v for v in qv.values())) or 1.0
        scores: list[float] = []
        for toks in docs_tokens:
            dv = _vec(toks)
            dot = sum(qv.get(t, 0.0) * w for t, w in dv.items())
            dnorm = math.sqrt(sum(v * v for v in dv.values())) or 1.0
            scores.append(dot / (qnorm * dnorm))
        return scores
    except Exception:
        logger.exception("_cosine_tfidf_scores failed — returning zeros")
        return [0.0] * len(docs_tokens)


def _load_corpus_sync(path: Path) -> list[dict]:
    """Read a JSONL file and return a list of entry dicts. Returns [] on any error."""
    if not path.exists():
        return []
    entries: list[dict] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("rag_store: skipping corrupt JSONL line in %s", path)
    except OSError as exc:
        logger.warning("rag_store: could not read corpus file %s: %s", path, exc)
    return entries


def _save_entry_sync(path: Path, entry: dict) -> None:
    """Append a single entry as a JSONL line. Creates parent dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _corpus_path(entry_type: str) -> Path:
    base = Path(settings.qa_rag_storage_path)
    if entry_type == "test_case":
        return base / "test_cases.jsonl"
    if entry_type == "bug_report":
        return base / "bug_reports.jsonl"
    return base / f"{entry_type}.jsonl"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def add_to_corpus(entry_type: str, content: str, metadata: dict) -> dict:
    """Append a new entry to the corpus.

    Returns {"error": None, "content": {"id": str}} on success.
    Returns {"error": str, "content": None} on any exception.
    Never raises.
    """
    try:
        entry_id = str(uuid.uuid4())
        entry = {
            "id": entry_id,
            "type": entry_type,
            "content": content,
            "metadata": metadata or {},
            "added_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        path = _corpus_path(entry_type)
        await asyncio.to_thread(_save_entry_sync, path, entry)
        logger.info("rag_store: added %s entry %s to %s", entry_type, entry_id, path)
        return {"error": None, "content": {"id": entry_id}}
    except Exception as exc:
        logger.exception("rag_store.add_to_corpus failed")
        return {"error": str(exc), "content": None}


async def query_corpus(
    query_text: str,
    entry_type: str | None = None,
    top_k: int = 5,
) -> dict:
    """Find the top-k corpus entries most similar to query_text.

    Similarity is Jaccard overlap on whitespace+punctuation tokens.

    Returns {"error": None, "content": list[{"content": str, "metadata": dict, "score": float}]}
    on success (list may be empty if corpus is empty or query produces no tokens).
    Returns {"error": str, "content": None} on any exception.
    Never raises.
    """
    try:
        if not query_text or not query_text.strip():
            return {"error": None, "content": []}

        query_tokens = _tokenize(query_text)
        if not query_tokens:
            return {"error": None, "content": []}

        if entry_type is not None:
            paths = [_corpus_path(entry_type)]
        else:
            paths = [_corpus_path("test_case"), _corpus_path("bug_report")]

        all_entries: list[dict] = []
        for path in paths:
            entries = await asyncio.to_thread(_load_corpus_sync, path)
            all_entries.extend(entries)

        if not all_entries:
            return {"error": None, "content": []}

        usable = [e for e in all_entries if (e.get("content") or "")]

        mode = (
            getattr(settings, "qa_rag_similarity_mode", "jaccard") or "jaccard"
        ).lower()
        if mode == "cosine":
            # TF-IDF cosine gives rare/discriminative terms more weight than
            # Jaccard's flat set overlap — better "we already have this" dedup.
            docs_tokens = [_tokenize_list(e["content"]) for e in usable]
            cos_scores = _cosine_tfidf_scores(_tokenize_list(query_text), docs_tokens)
            scored = list(zip(cos_scores, usable))
        else:
            scored = [
                (_jaccard_similarity(query_tokens, _tokenize(e["content"])), e)
                for e in usable
            ]

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:top_k]

        results = [
            {
                "content": entry.get("content", ""),
                "metadata": entry.get("metadata", {}),
                "score": round(score, 4),
            }
            for score, entry in top
            if score > 0
        ]

        logger.info(
            "rag_store.query_corpus: query returned %d/%d results (top score %.4f)",
            len(results),
            len(all_entries),
            results[0]["score"] if results else 0.0,
        )
        return {"error": None, "content": results}
    except Exception as exc:
        logger.exception("rag_store.query_corpus failed")
        return {"error": str(exc), "content": None}
