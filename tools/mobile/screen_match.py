"""Fuzzy screen matching: the fallback when a screen has no exact stored key.

Two screens of one app that differ by a banner or a list row share almost all
their (class, resource-id) pairs. Element-set Jaccard at ``SCREEN_MATCH_JACCARD``
or more calls them one screen; the caller records the old key as an alias
(``screens.remap_of``). Pure functions, no I/O, never raises.
"""

from __future__ import annotations

from typing import Any

#: Element-set (or anchor-set) Jaccard at which two screens are one screen.
SCREEN_MATCH_JACCARD = 0.85


def element_set(elements: Any) -> frozenset:
    """The (class, resource-id) pairs of a perception element list."""
    out = set()
    for e in elements if isinstance(elements, list) else []:
        if isinstance(e, dict):
            out.add((str(e.get("cls") or ""), str(e.get("rid") or "")))
    return frozenset(out)


def jaccard(a: Any, b: Any) -> float:
    """Jaccard overlap of two collections; 0.0 when both are empty."""
    try:
        sa, sb = set(a or ()), set(b or ())
    except TypeError:
        return 0.0
    union = sa | sb
    return len(sa & sb) / len(union) if union else 0.0


def nearest(sets: Any, known: dict) -> str:
    """The key in *known* (``{key: set}``) closest to *sets* at or above the
    threshold; ``""`` when none qualifies or the best score is tied."""
    scored = sorted(((jaccard(sets, v), k) for k, v in known.items()), reverse=True)
    if not scored or scored[0][0] < SCREEN_MATCH_JACCARD:
        return ""
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return ""
    return str(scored[0][1])
