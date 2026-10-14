"""How a visible label is matched against a node's strings: normalised, exact then contains."""

from __future__ import annotations

import re
from typing import NamedTuple

TIER_EXACT = "exact"
TIER_CONTAINS = "contains"

_WORDS = re.compile(r"\w+", re.UNICODE)


class _Query(NamedTuple):
    label: str
    tier: str


def _norm(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def _words(value: object) -> list[str]:
    return _WORDS.findall(_norm(value))


def _contains(wanted: list[str], have: list[str]) -> bool:
    size = len(wanted)
    return bool(size) and any(
        have[i : i + size] == wanted for i in range(len(have) - size + 1)
    )


def _strings(element: dict, keys: tuple) -> list[str]:
    return [s for s in (str(element.get(k) or "") for k in keys) if s.strip()]


def _matches(element: dict, keys: tuple, query: _Query) -> bool:
    if query.tier == TIER_EXACT:
        return any(_norm(s) == query.label for s in _strings(element, keys))
    wanted = _words(query.label)
    return any(_contains(wanted, _words(s)) for s in _strings(element, keys))
