"""Size caps for the EN/AR bilingual rule pack (home of both scalars)."""

from __future__ import annotations

# Deliberately a module constant, not a settings field: it is a safety cap on a
# parser, not an operator knob (same rubric-vs-knob reasoning as
# tools/atomic_checklist._SHORT_ITEM_WORDS).
MAX_PAIRS = 40
_MAX_VALUE_CHARS = 200
