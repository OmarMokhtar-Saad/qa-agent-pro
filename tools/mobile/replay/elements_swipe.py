"""Replay swipe geometry: the confined gesture and the touch point the guard judges."""

from __future__ import annotations

from typing import Any, NamedTuple

from tools.mobile.replay.elements_geom import (
    _center,
    _on_screen_box,
)

#: ``dir`` is the CONTENT direction, like ``scroll_down`` of the device UI:
#: "down" reveals what is below, so the finger moves UP. Each row is the finger.
_SWIPE = {
    "down": (540, 1500, 540, 500),
    "up": (540, 500, 540, 1500),
    "right": (900, 1000, 200, 1000),
    "left": (200, 1000, 900, 1000),
}

#: How far inside a named element's edge a confined swipe starts and ends, in
#: pixels. Keeps the finger off the border, where a touch belongs to the
#: neighbour as often as to the element.
_SWIPE_INSET = 10

#: The shortest gesture that is still a SWIPE to Android. Below touch slop
#: (8-32 px by density) the framework delivers a click, and a 300 ms press is
#: under the long-press threshold -- so a 2 px "swipe" on a 22 px element was
#: a tap on it, measured by an executing review. Chosen from the platform's
#: slop, not from :data:`_SWIPE_INSET`; the two are unrelated quantities.
_MIN_SWIPE_PX = 24


def _centred_swipe(direction: str, points: tuple, box: tuple) -> tuple:
    """The swipe for *direction* centred in *box*, its half-extent the table's
    (*points*) clipped to the box minus :data:`_SWIPE_INSET`."""
    x1, y1, x2, y2 = box
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    if direction in ("up", "down"):
        half = min(
            abs(points[1] - points[3]) // 2, max(0, (y2 - y1) // 2 - _SWIPE_INSET)
        )
        if direction == "down":
            return (cx, cy + half, cx, cy - half)
        return (cx, cy - half, cx, cy + half)
    half = min(abs(points[0] - points[2]) // 2, max(0, (x2 - x1) // 2 - _SWIPE_INSET))
    if direction == "right":
        return (cx + half, cy, cx - half, cy)
    return (cx - half, cy, cx + half, cy)


# The swipe ``_perform`` issues for *direction*, confined to *element*.
#
# A target-less pan uses the :data:`_SWIPE` table as it is. A TARGETED swipe
# is centred on the element on BOTH axes and clipped to the element's bounds
# minus :data:`_SWIPE_INSET`, so the whole gesture happens on the node the
# action named (shifting by X only once swiped past the named row and across
# controls the guard never judged). A short element gets a short swipe.
#
# THE INVARIANT, checked LAST on the final tuple: the gesture moves at least
# :data:`_MIN_SWIPE_PX` along its axis, or ``None`` is returned. Checking
# before a clamp once let a mostly off-screen element ship a press.
#
# The element's ON-SCREEN part (:func:`_on_screen_box`) is FOUR clamps when
# *display* is known, two when not. *display* is the packet's own
# ``root_bounds``, so the guard and the device read one number; absent or
# unusable, the far clamps are SKIPPED rather than guessed. A partly-visible
# row is the NORMAL case: its far edges can lie past the display, and a
# gesture there is silently ignored while the step reports success.
#
# Returns ``None`` for an unknown direction, and for an element whose
# on-screen part is too small to swipe on; ``_perform`` tells the two apart
# and reports each.
def _swipe_points(direction: str, element: Any, display: Any = None) -> tuple | None:
    """The swipe ``_perform`` issues for *direction*, confined to *element*."""
    points = _SWIPE.get(str(direction or ""))
    if not points:
        return None
    if not isinstance(element, dict):
        return points
    center = _center(element)
    if center is None:
        return points
    x1, y1, x2, y2 = _on_screen_box(element, display)
    if x2 <= x1 or y2 <= y1:
        return None
    swipe = _centred_swipe(direction, points, (x1, y1, x2, y2))
    # The post-condition, on the FINAL tuple: a gesture that moves less than
    # touch slop is delivered as a click on the element the script asked to
    # scroll. Handed back instead, so the model names the list.
    if abs(swipe[2] - swipe[0]) + abs(swipe[3] - swipe[1]) < _MIN_SWIPE_PX:
        return None
    return swipe


class TouchSpec(NamedTuple):
    """The op, action, element and display a touch point is judged for."""

    op: str
    action: Any
    element: Any
    display: Any = None


def _touch_point(spec: TouchSpec) -> tuple | None:
    """Where the finger lands FIRST for *op* on *element* -- the point the
    destructive guard judges. Derived from the same helpers ``_perform`` uses,
    so the guard and the device cannot disagree about where the touch is.

    *display* rides along for exactly that reason: it changes where a clamped
    swipe STARTS, so the guard has to judge the clamped point, not the
    unclamped one."""
    if spec.op == "scroll":
        points = _swipe_points(
            str(getattr(spec.action, "dir", "") or ""), spec.element, spec.display
        )
        return (points[0], points[1]) if points else None
    if isinstance(spec.element, dict):
        return _center(spec.element)
    return None
