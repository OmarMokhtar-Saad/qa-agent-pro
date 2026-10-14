"""Naming an element: its own label, else the largest child it wraps."""

from __future__ import annotations

from typing import Any

from tools.mobile.perception_limits import MIN_LABEL_AREA_SHARE


def _own_label(element: object) -> str:
    """What this element says about ITSELF. ``content-desc`` first.

    ``desc`` beats ``text`` because an icon-only control has only a desc, and
    where both exist the desc is the accessible name -- the thing a tester would
    call the control.
    """
    if not isinstance(element, dict):
        return ""
    desc = str(element.get("desc") or "").strip()
    return desc or str(element.get("text") or "").strip()


def _bounds_tuple(element: object) -> tuple | None:
    box = (element or {}).get("bounds") if isinstance(element, dict) else None
    if not (isinstance(box, (list, tuple)) and len(box) == 4):
        return None
    try:
        return tuple(int(value) for value in box)
    except (TypeError, ValueError, OverflowError):
        return None


def _contained_area(other: object, outer: tuple) -> int | None:
    """The area of *other* when its bounds lie inside *outer*, else ``None``."""
    inner = _bounds_tuple(other)
    if inner is None:
        return None
    x1, y1, x2, y2 = outer
    a1, b1, a2, b2 = inner
    if a1 < x1 or b1 < y1 or a2 > x2 or b2 > y2:
        return None
    return max(0, a2 - a1) * max(0, b2 - b1)


def _child_label(other: dict, outer: tuple, floor: float) -> tuple[int, str] | None:
    """``(area, label)`` of a contained child big enough to name *outer*, else ``None``."""
    area = _contained_area(other, outer)
    if area is None or area < floor:
        return None
    text = _own_label(other)
    return (area, text) if text else None


# The LARGEST qualifying child, not the smallest: among the children
# that fill enough of this control to be its label, the biggest is the
# one the control is drawn around.
def _largest_contained_label(element: object, outer: tuple, elements: Any) -> str:
    """The label of the largest contained element that fills enough of *outer*."""
    x1, y1, x2, y2 = outer
    floor = max(0, x2 - x1) * max(0, y2 - y1) * MIN_LABEL_AREA_SHARE
    best = ""
    best_area = None
    for other in list(elements or []):
        if not isinstance(other, dict) or other is element:
            continue
        found = _child_label(other, outer, floor)
        if found is not None and (best_area is None or found[0] > best_area):
            best_area, best = found
    return best


# The name a tester would give *element*, borrowing a child's if it has none.
#
# The clickable thing and the labelled thing are OFTEN different nodes: on the
# 2026-09-04 screen the Send affordance is a clickable ``View`` with empty
# text and desc, wrapping a non-clickable child whose desc is "Send". Three
# controls on that screen looked identical in the packet for exactly this
# reason.
#
# Only an element with no label of its own borrows one, only if it is itself
# TAPPABLE, and only from a CONTAINED element whose own area covers at least
# :data:`MIN_LABEL_AREA_SHARE` of it. The lower bound is load-bearing:
# without it the root FrameLayout of the fixture -- which contains every word
# on the screen -- took the label of whichever contained element was
# smallest, and the packet then named the whole screen "Send".
#
# CONTAINED, NOT STRICTLY SMALLER. An equal-bounds child counts, and used not
# to. ``match_parent`` x ``match_parent`` is the commonest button shape on
# Android -- a clickable wrapper with an empty label around a TextView filling
# it exactly -- so excluding equal area excluded the ordinary case, and the
# control reached the packet unlabelled while the tester was looking straight
# at its name.
#
# There is no upper AREA bound, because there cannot be one that does
# anything: the containment test above already requires the child to lie
# inside this element, and a box inside another box cannot have the larger
# area. The rule carried ``area >= own_area`` for exactly one reachable case
# -- equal area -- and once that became legitimate the clause was dead. A
# mutation removing it survived, which is how it was found: an unreachable
# condition reads as a safeguard and grades as nothing. What actually bounds
# this from above is CONTAINMENT, and that is where the test aims.
#
# This rule and the destructive guard's (``executor.contained_texts``) read
# the same geometry for different purposes, and they DIVERGED here: the guard
# was fixed to include equal area after a transfer CTA tapped through
# unguarded, and this mirror of it was not. No safety gap -- the guard reads
# containment itself and never consults this -- but the packet the model
# plans from disagreed with the packet the guard judged. Pinned by
# ``tests/mobile/test_mobile_label_containment.py``, which asserts the two
# rules agree rather than asserting each one separately.
def label_of(element: object, elements: object) -> str:
    """The name a tester would give *element*, borrowing a child's if it has none."""
    own = _own_label(element)
    if own:
        return own
    if not (isinstance(element, dict) and element.get("clickable")):
        return ""
    outer = _bounds_tuple(element)
    if outer is None:
        return ""
    return _largest_contained_label(element, outer, elements)
