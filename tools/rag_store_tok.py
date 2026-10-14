"""Tokenizers and set similarity for the RAG store (pure, no I/O)."""

from __future__ import annotations

import re


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
