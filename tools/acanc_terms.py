"""AC-anchor scope-term helpers: stopwords and content-word extraction."""

from __future__ import annotations

import logging
import re

from tools.models import TestCase

# literal name: log records stay under the pre-split module name
logger = logging.getLogger("tools.ac_anchor")

_WORD_RE = re.compile(r"[a-z0-9]+")

# Deliberately small and generic: test-boilerplate and grammar words that carry
# no scope signal in either direction.
_SCOPE_STOPWORDS = frozenset(
    {
        "that",
        "this",
        "with",
        "from",
        "into",
        "have",
        "will",
        "must",
        "when",
        "then",
        "than",
        "them",
        "they",
        "there",
        "these",
        "those",
        "should",
        "verify",
        "check",
        "ensure",
        "confirm",
        "test",
        "case",
        "user",
        "users",
        "given",
        "while",
        "after",
        "before",
        "each",
        "also",
        "only",
        "does",
        "been",
        "being",
        "using",
        "used",
        "which",
        "where",
        "what",
        "some",
        "such",
        "same",
        "other",
        "value",
        "values",
        "shown",
        "correct",
        "valid",
        "invalid",
        "system",
        "screen",
        "page",
    }
)


def _terms(text: str) -> set[str]:
    """Lower-cased content words (>= 4 chars, non-stopword) of *text*.

    Crude plural folding (trailing "s" on words longer than 4 chars) so
    "refunds" and "refund" match. Never raises.
    """
    try:
        out: set[str] = set()
        for raw in _WORD_RE.findall((text or "").lower()):
            if len(raw) < 4 or raw in _SCOPE_STOPWORDS:
                continue
            out.add(raw[:-1] if len(raw) > 4 and raw.endswith("s") else raw)
        return out
    except Exception:
        logger.exception("_terms failed — treating the text as empty")
        return set()


def _case_terms(tc: TestCase) -> set[str]:
    """Content words of a case's title + step actions + expected results."""
    chunks = [getattr(tc, "title", "") or ""]
    for step in getattr(tc, "steps", None) or []:
        chunks.append(getattr(step, "action", "") or "")
        chunks.append(getattr(step, "expected_result", "") or "")
    return _terms(" ".join(chunks))
