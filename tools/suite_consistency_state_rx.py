"""State, availability and subject patterns for contradictory-state detection."""

from __future__ import annotations

import re

# --- Contradictory state assumptions ---------------------------------------

# A test_data field naming the state/status an entity is seeded in.
STATE_FIELD_RE = re.compile(r"stat(?:us|e)", re.IGNORECASE)

# The case expects the action to BE offered.
_ACTION_AVAILABLE_RE = re.compile(
    r"\b(?:is|remains|stays|still)\s+(?:visible|available|enabled|offered|shown|present|tappable)\b"
    r"|\bis\s+visible\s+and\s+enabled\b",
    re.IGNORECASE,
)
# The case expects the action NOT to be offered.
_ACTION_BLOCKED_RE = re.compile(
    r"\bis\s+not\s+(?:visible|available|offered|shown|present)\b"
    r"|\bis\s+(?:hidden|disabled|unavailable)\b"
    r"|\bno\s+longer\s+(?:shown|offered|available)\b"
    r"|\bcannot\s+(?:open|start|be\s+cancel\w+)\b",
    re.IGNORECASE,
)

# Terminal states where "the action is gone" is the CORRECT expectation for
# every case, so an available/blocked split across them is not a contradiction.
_TERMINAL_STATE_TOKENS = {
    "cancelled",
    "canceled",
    "ملغي",
    "completed",
    "closed",
    "refunded",
}


# A `key: value` pair inside a step's free-text test_data string. Status is
# frequently carried there rather than as a structured item -- "Step 1: order_id:
# ORD-KEEP-001, initial_status: Processing" -- so both sources are scanned.
_INLINE_PAIR_RE = re.compile(r"([A-Za-z][A-Za-z0-9_ ]{0,30})\s*:\s*([^,;\n]{1,40})")

# Words that carry no identity when comparing which control a sentence is about.
_SUBJECT_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "no",
        "not",
        "and",
        "or",
        "but",
        "for",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "on",
        "in",
        "at",
        "to",
        "of",
        "with",
        "from",
        "still",
        "remains",
        "stays",
        "longer",
        "any",
        "all",
        "user",
        "users",
        "screen",
        "page",
        "app",
        # Structural words that appear in almost every expected result here, so
        # an overlap on them says nothing about the two cases being related --
        # "status" alone was matching a pair that shared no control at all.
        "status",
        "state",
        "step",
        "current",
        "per",
        "rules",
        "rule",
        "build",
        "equivalent",
        "shown",
        "unchanged",
        "detail",
        "details",
        "option",
    }
)
# NOTE: the apostrophe is deliberately NOT part of a word. Quoted control names
# are the norm in these expected results ("'Cancel order' is still visible"), and
# including the quote produced tokens like "'cancel" / "order'" that could never
# match the same words written unquoted -- silently disabling the whole check.
_SUBJECT_WORD_RE = re.compile(r"[A-Za-z\u0600-\u06ff]+")

_STATE_FIELD_RE = STATE_FIELD_RE  # legacy name (WX removes it)
