"""Device extent of a stored screen for the report wireframe."""

from __future__ import annotations

from typing import Any


# Inverted bounds are SORTED rather than rejected: an app that reports
# ``[400,900,100,200]`` has still told us where its control is, and a negative
# width would otherwise reach the geometry.
def _bounds_of(element: object) -> tuple | None:
    """``[x1,y1,x2,y2]`` -> a normalised tuple, or None."""
    body = element if isinstance(element, dict) else {}
    raw = body.get("bounds")
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    numbers = []
    for value in raw:
        if isinstance(value, bool):
            return None
        try:
            numbers.append(int(float(value)))
        except (TypeError, ValueError, OverflowError):
            return None
    left, right = sorted((numbers[0], numbers[2]))
    top, bottom = sorted((numbers[1], numbers[3]))
    return (left, top, right, bottom)


# The largest device extent this module accepts out of a store file. The WRITER
# cannot produce more -- `perception.parse_bounds` matches at most seven digits
# -- so a larger value in a store file did not come from a dump this server
# pruned. `scale_bounds` keeps its no-raise guarantee either way; this is the
# repo's "every cap is bounded from above" convention applied to a value whose
# own docstring calls it untrusted input.
MAX_DEVICE_EXTENT = 9999999


# Not a hardcoded phone size: a tablet AVD is legitimate. ``(0, 0)`` when the
# dump carries no usable geometry, which is what stops the divide.
#
# **The stored root is preferred because ``max()`` is not the viewport.** A
# ``RecyclerView``/``ScrollView`` reports its CONTENT height, so on a scrollable
# screen the lowest element bottom routinely exceeds the display -- and
# :func:`scale_bounds`, which scales by ``min(MAX_FRAME_W/dev_w, MAX_FRAME_H/dev_h)``,
# then shrinks EVERY element to fit a device taller than the real one. The
# visible result is every scrollable screen squeezed into the top of its frame.
#
# ``perception.prune`` stores that rectangle as ``root_bounds``: the device's
# own display, from ``adb.display_size``, oriented by the dump's ``rotation``
# -- NOT a window chosen out of the dump. Four rounds of choosing one are in
# docs/DECISIONS.md -> *The display size is not in the dump*. RIGHT and BOTTOM
# are the size: the origin is always ``(0, 0)``, so the extent measured from
# it is what :func:`scale_bounds` maps against.
#
# Where the device could not answer AND no depth-1 window was measurable, the
# panel is genuinely unknown and ``root_bounds`` is ``[]`` -- which since the
# panel stopped borrowing the frame's rule is reachable with a FULL element
# list, not just on an empty dump. So ``frame_bounds`` is read next: the
# rectangle ``prune`` actually filtered with, which is at least as large as
# everything laid out. It is not the display and does not claim to be; it is a
# scale the PRODUCER derived rather than one this function improvises.
#
# The ``max()`` walk survives below as the last resort for one caller that
# still happens: a screen stored by a build that wrote neither key. It must
# not become the ordinary path for an unknown panel -- it walks the pruned
# ELEMENTS, so on a scrollable screen it returns the container's content
# height, and that is the squeeze described above.
#
# This is a store file -- an outside input by the time it is read -- so a wrong
# type, a wrong length, a bool, a non-number, a negative origin or a zero-area
# rect must all DEGRADE to the fallback rather than raise or hand back a zero
# divisor. An INVERTED rect does not degrade: :func:`_bounds_of` sorts
# left/right and top/bottom deliberately, so ``[1080, 2400, 0, 0]`` normalises
# to ``(0, 0, 1080, 2400)`` and is accepted. That is the helper's existing,
# documented behaviour and it is pinned by test rather than changed here.
def _device_size(screen: object) -> tuple:
    """The device viewport: the stored display rectangle, or the elements' extent."""
    body = screen if isinstance(screen, dict) else {}
    # Priority order, one set of clauses: display, then filter frame, then the walk.
    for key in ("root_bounds", "frame_bounds"):
        size = _stored_extent(body.get(key))
        if size is not None:
            return size
    return _elements_extent(body.get("elements"))


def _stored_extent(value: object) -> tuple | None:
    """``(right, bottom)`` of a stored rectangle, or None when it is unusable."""
    rect = _bounds_of({"bounds": value})
    if (
        rect is not None
        and rect[0] >= 0
        and rect[1] >= 0
        and 0 < rect[2] <= MAX_DEVICE_EXTENT
        and 0 < rect[3] <= MAX_DEVICE_EXTENT
    ):
        # Each clause is reachable on its own, which is what makes it a
        # guard rather than decoration. `_bounds_of` SORTS, so
        # `rect[2] >= rect[0]` always holds -- but with the origin only
        # required to be non-negative, `[0,0,0,2400]` still reaches and
        # fails `rect[2] > 0`, and `[0,0,1080,0]` still reaches and fails
        # `rect[3] > 0`. A negative origin fails the first two and never
        # reaches either.
        return rect[2], rect[3]
    return None


def _elements_extent(elements: Any) -> tuple:
    """The lowest-right corner over the elements: the last-resort device size."""
    width = 0
    height = 0
    for element in elements or []:
        box = _bounds_of(element)
        if box is None:
            continue
        width = max(width, box[2])
        height = max(height, box[3])
    return width, height
