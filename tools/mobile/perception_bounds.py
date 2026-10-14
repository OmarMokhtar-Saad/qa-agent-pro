"""Bounds parsing and the geometry questions asked of raw nodes and elements."""

from __future__ import annotations

import re
from typing import Any

_BOUNDS_RE = re.compile(r"\[(-?\d{1,7}),(-?\d{1,7})\]\[(-?\d{1,7}),(-?\d{1,7})\]")


def parse_bounds(raw: object) -> tuple[int, int, int, int] | None:
    """``[x1,y1][x2,y2]`` -> a 4-tuple, or None when it is not that shape."""
    match = _BOUNDS_RE.search(str(raw or ""))
    if not match:
        return None
    x1, y1, x2, y2 = (int(match.group(index)) for index in (1, 2, 3, 4))
    return x1, y1, x2, y2


def _area(bounds: tuple[int, int, int, int]) -> int:
    return max(0, bounds[2] - bounds[0]) * max(0, bounds[3] - bounds[1])


def _extent(nodes: list) -> tuple[int, int]:
    """The largest right and bottom edge over the positive-area nodes."""
    right = 0
    bottom = 0
    for node in nodes:
        bounds = parse_bounds(node.get("bounds"))
        if bounds is None or _area(bounds) <= 0:
            continue
        right = max(right, bounds[2])
        bottom = max(bottom, bounds[3])
    return right, bottom


def _package_area(element: object) -> tuple[str, int] | None:
    """``(package, area)`` of one element, or None when it cannot be ranked."""
    if not isinstance(element, dict):
        return None
    name = str(element.get("package") or "")
    bounds = element.get("bounds") or []
    if not name or len(bounds) != 4:
        return None
    try:
        x1, y1, x2, y2 = (int(value) for value in bounds)
    except (TypeError, ValueError, OverflowError):
        return None
    return name, max(0, x2 - x1) * max(0, y2 - y1)


# Whose screen this is: the package of the element covering the most of it.
#
# NOT the first node's, which is what it used to be. A uiautomator dump holds
# every window, and when a system overlay sorted first the whole screen was
# renamed after it -- so the left-the-app check fired on every settle, told
# the model to `launch` (which changed nothing, because the next dump looked
# identical), and spent all three of the case's escapes on an unhelpful
# recovery.
#
# Area is the right discriminator because an overlay is, by definition, drawn
# over part of a window that is larger than it. A tie keeps the earlier
# element, so the answer is stable for a screen that really is one window.
def _dominant_package(elements: Any) -> str:
    """Whose screen this is: the package of the element covering the most of it."""
    best = ""
    best_area = -1
    for element in list(elements or []):
        ranked = _package_area(element)
        if ranked is not None and ranked[1] > best_area:
            best, best_area = ranked
    return best
