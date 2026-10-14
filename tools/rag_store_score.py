"""TF-IDF cosine and Okapi BM25 scoring for the RAG store (pure, no I/O)."""

from __future__ import annotations

import logging
import math
from collections import Counter

logger = logging.getLogger("tools.rag_store")

_K1 = 1.2
_B = 0.75


def _doc_freq(corpus: list[list[str]]) -> Counter:
    df: Counter = Counter()
    for toks in corpus:
        for t in set(toks):
            df[t] += 1
    return df


def _idf_smooth(t: str, n: int, df: Counter) -> float:
    return math.log((n + 1) / (df.get(t, 0) + 1)) + 1.0


def _tfidf_vec(toks: list[str], n: int, df: Counter) -> dict:
    if not toks:
        return {}
    tf = Counter(toks)
    total = len(toks)
    return {t: (c / total) * _idf_smooth(t, n, df) for t, c in tf.items()}


def _norm(vec: dict) -> float:
    return math.sqrt(sum(v * v for v in vec.values())) or 1.0


def _cos(qv: dict, qnorm: float, dv: dict) -> float:
    dot = sum(qv.get(t, 0.0) * w for t, w in dv.items())
    return dot / (qnorm * _norm(dv))


# Cosine similarity between a query and each doc under a TF-IDF weighting
# computed over the docs + query (opt-in dedup mode, I-051). Pure Python, no
# external model. Returns one score in [0, 1] per doc. Never raises.
def _cosine_tfidf_scores(
    query_tokens: list[str], docs_tokens: list[list[str]]
) -> list[float]:
    try:
        corpus = docs_tokens + [query_tokens]
        n, df = len(corpus), _doc_freq(corpus)
        qv = _tfidf_vec(query_tokens, n, df)
        qnorm = _norm(qv)
        return [_cos(qv, qnorm, _tfidf_vec(toks, n, df)) for toks in docs_tokens]
    except Exception:
        logger.exception("_cosine_tfidf_scores failed — returning zeros")
        return [0.0] * len(docs_tokens)


def _bm25_idf(t: str, n: int, df: Counter) -> float:
    d = df.get(t, 0)
    return math.log(1.0 + (n - d + 0.5) / (d + 0.5))


def _bm25_doc_score(
    query_tokens: list[str], toks: list[str], stats: tuple[int, Counter, float]
) -> float:
    n, df, avgdl = stats
    tf = Counter(toks)
    dl = len(toks) or 1
    s = 0.0
    for t in query_tokens:
        f = tf.get(t, 0)
        if not f:
            continue
        s += (
            _bm25_idf(t, n, df)
            * (f * (_K1 + 1))
            / (f + _K1 * (1 - _B + _B * dl / avgdl))
        )
    return s / (s + 10.0) if s > 0 else 0.0


# Okapi BM25 — the consensus lexical retrieval baseline: term-frequency
# saturation + document-length normalization outrank flat overlap and
# TF-IDF on technical text (steps, identifiers, feature names).
#
# Raw BM25 is unbounded, so scores are saturation-normalized to [0, 1)
# via s/(s+10) — the existing qa_rag_similarity_threshold semantics keep
# working across modes (a raw BM25 of ~4.3 ≈ 0.3 normalized). Pure Python,
# default parameters per the literature; never raises.
def _bm25_scores(query_tokens: list[str], docs_tokens: list[list[str]]) -> list[float]:
    try:
        n = len(docs_tokens)
        if n == 0:
            return []
        avgdl = (sum(len(d) for d in docs_tokens) / n) or 1.0
        stats = (n, _doc_freq(docs_tokens), avgdl)
        return [_bm25_doc_score(query_tokens, toks, stats) for toks in docs_tokens]
    except Exception:
        logger.exception("_bm25_scores failed — returning zeros")
        return [0.0] * len(docs_tokens)
