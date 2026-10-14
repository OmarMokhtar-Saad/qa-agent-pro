"""Reading the value a test step types, for ``bound_consistency``."""

from __future__ import annotations

import re

from config.settings_guard import never_raise
from tools.bcons_text import _number

_NUM = r"(\d[\d,]*(?:\.\d+)?)"

# The value the tester types. A quoted literal must be numeric IN FULL -- the
# SQL payload "100; DROP TABLE cards;--" starts with a legal amount and is not
# one. Otherwise the number must carry a currency marker.
_ENTER_VERB_RE = re.compile(
    r"\b(?:enter|enters|input|inputs|type|types|set|sets)\b", re.IGNORECASE
)
_QUOTED_RE = re.compile(r"['\"‘’“”]([^'\"‘’“”]{1,40})['\"‘’“”]")
_PURE_NUMERIC_RE = re.compile(r"^[A-Za-z]{0,4}\s*\d[\d,]*(?:\.\d+)?$")
_CURRENCY_RE = re.compile(r"(?:[A-Z]{3}|[$£€¥])\s*" + _NUM + r"(?![\w.,]*[A-Za-z])")

# How far past the enter verb the value may sit.
_VALUE_WINDOW = 80


def _value_candidates(tail: str) -> list[tuple[int, float]]:
    candidates: list[tuple[int, float]] = []
    quoted = _QUOTED_RE.search(tail)
    if quoted:
        literal = quoted.group(1).strip()
        if _PURE_NUMERIC_RE.match(literal):
            candidates.append(
                (quoted.start(), _number(re.sub(r"[A-Za-z\s]", "", literal)))
            )
    currency = _CURRENCY_RE.search(tail)
    if currency:
        candidates.append((currency.start(), _number(currency.group(1))))
    return candidates


def entered_value(action: str) -> float | None:
    """The number this action types into a field, or None when unreadable."""
    with never_raise():
        verb = _ENTER_VERB_RE.search(action or "")
        if not verb:
            return None
        tail = action[verb.end() : verb.end() + _VALUE_WINDOW]
        candidates = _value_candidates(tail)
        return min(candidates)[1] if candidates else None
    return None
