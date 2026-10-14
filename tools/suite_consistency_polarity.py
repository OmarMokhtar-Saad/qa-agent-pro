"""What a case expects of an action (available or blocked), and about which control."""

from __future__ import annotations

import re

from tools.models import TestCase
from tools.suite_consistency_state_rx import (
    _ACTION_AVAILABLE_RE,
    _ACTION_BLOCKED_RE,
    _SUBJECT_STOPWORDS,
    _SUBJECT_WORD_RE,
)


def _subject_terms(text: str, match: re.Match) -> set[str]:
    """Identity words of the control a polarity phrase is talking about.

    Taken from the words immediately BEFORE the match, which in practice is the
    subject noun phrase ("the 'Cancel order' option is visible" -> cancel,
    order, option).
    """
    prefix = text[max(0, match.start() - 90) : match.start()]
    words = [w.lower().strip("'\"") for w in _SUBJECT_WORD_RE.findall(prefix)]
    return {w for w in words[-6:] if w and w not in _SUBJECT_STOPWORDS and len(w) > 2}


def _subjects_of(text: str, matches: list[re.Match]) -> set[str]:
    # EVERY match contributes, not just the first: a case that says both
    # "success screen is not shown" and "Cancel is unavailable" is about the
    # cancel control too, and reading only the first match dropped two of the
    # three cases in the real Processing contradiction.
    out: set[str] = set()
    for match in matches:
        out |= _subject_terms(text, match)
    return out


def _polarity_of(text: str) -> tuple[str, set[str]] | None:
    """(polarity, subject terms), or None when the text asserts neither/both.

    The subject terms exist because polarity ALONE is not a contradiction: a
    suite that says "a success toast is visible" in one case and "the refund
    banner is not shown" in another was reported as disagreeing about whether the
    seeded status permits the action, which it plainly was not. Two cases now
    only conflict when they speak about the same control.
    """
    blocked = list(_ACTION_BLOCKED_RE.finditer(text))
    available = list(_ACTION_AVAILABLE_RE.finditer(text))
    if blocked and not available:
        return "blocked", _subjects_of(text, blocked)
    if available and not blocked:
        return "available", _subjects_of(text, available)
    return None


def _expected_polarity(tc: TestCase) -> tuple[str, set[str]] | None:
    """'available' / 'blocked' / None -- what the case expects of the action.

    When the case asserts both across its steps, the FINAL step decides: that is
    the case's conclusion, and earlier steps routinely describe the starting
    state ("Cancel order is available") before the action changes it. A case
    whose last step is itself ambiguous yields None and takes no part in
    contradiction detection -- it is already reported by
    :func:`find_nondeterministic_oracles`.
    """
    try:
        steps = list(getattr(tc, "steps", None) or [])
        if not steps:
            return None
        joined = " ".join((s.expected_result or "") for s in steps)
        overall = _polarity_of(joined)
        return overall or _polarity_of(steps[-1].expected_result or "")
    except (AttributeError, TypeError):
        return None
