"""The wireframe of one pruned screen: scaled rectangles and what fell off the display."""

from __future__ import annotations

from typing import Any

from tools.mobile.report_text import _text
from tools.mobile.rfrm_box import _frame_box, scale_bounds
from tools.mobile.rfrm_extent import _bounds_of, _device_size

#: ``perception.MAX_ELEMENTS`` / ``MAX_ATTR_CHARS``, enforced INDEPENDENTLY here
#: because a store file is an outside input by the time this module reads it.
MAX_RECTS = 150


def _rect_of(element: object, placed: tuple) -> dict:
    """One drawn wireframe rectangle: scaled box, label and interaction kind."""
    holder = element if isinstance(element, dict) else {}
    label = _text(holder.get("text") or holder.get("desc") or holder.get("rid"), 40)
    kind = "plain"
    if holder.get("clickable"):
        kind = "tap"
    elif holder.get("editable"):
        kind = "edit"
    return {
        "x": placed[0],
        "y": placed[1],
        "w": placed[2],
        "h": placed[3],
        "label": label,
        "kind": kind,
    }


# ``{"rects", "device", "scaled", "clipped", "outside"}`` for one pruned
# screen. ``clipped`` counts elements drawn as the sliver that fits the
# display; ``outside`` counts elements with area but none of it on the
# display, which are not drawn; the page discloses both.
#
# ``scaled`` is False when the dump had no usable geometry -- the report then
# draws a labelled empty frame rather than a plausible-looking wrong one.
# `scaled` is a claim about the PICTURE, not about the geometry that went
# into it: this function's contract is that a False draws a labelled empty
# frame rather than a plausible-looking wrong one. A screen whose elements
# all lie outside the display -- reachable since the visibility frame was
# widened past it -- scales every one of them to nothing and would otherwise
# return an empty frame claiming to be a real one.
def wireframe(screen: object) -> dict:
    """The wireframe of one pruned screen: rects, device, scaled, clipped, outside."""
    body = screen if isinstance(screen, dict) else {}
    dev_w, dev_h = _device_size(body)
    rects, clipped, outside = _place_elements(body.get("elements"), dev_w, dev_h)
    frame_w, frame_h = _frame_box(dev_w, dev_h)
    return {
        "rects": rects,
        "device": [dev_w, dev_h],
        "scaled": bool(rects),
        "clipped": clipped,
        "outside": outside,
        "frame_w": frame_w,
        "frame_h": frame_h,
    }


# No zero-device check HERE: `scale_bounds` refuses a zero device for every
# box and the loop then produces no rects, which is the same frame the
# removed early return built by hand. Two checks for one condition was a
# second copy waiting to drift (round-13 review, R3/R6).
# Two names for two facts, counted BEFORE the scale decides whether
# anything is drawn (round-14 review, H1: counting after the
# `placed is None` skip omitted every element that lay entirely off
# the display, so a screen that lost 2 of 4 elements said "1").
#   clipped -- has area inside the display but reaches past it; drawn
#              as the sliver that fits.
#   outside -- has area, none of it on the display; not drawn at all.
# A degenerate (zero-area) box is neither: there is nothing to draw
# and nothing was lost.
def _place_elements(elements: Any, dev_w: int, dev_h: int) -> tuple:
    """``(rects, clipped, outside)`` for the elements scaled onto the frame."""
    rects = []
    clipped = 0
    outside = 0
    for element in list(elements or [])[:MAX_RECTS]:
        box = _bounds_of(element)
        if box is None:
            continue
        has_area = box[2] > box[0] and box[3] > box[1]
        past_edge = box[0] < 0 or box[1] < 0 or box[2] > dev_w or box[3] > dev_h
        placed = scale_bounds(box, dev_w, dev_h)
        if placed is None:
            if has_area and dev_w > 0 and dev_h > 0:
                outside += 1
            continue
        if past_edge:
            clipped += 1
        rects.append(_rect_of(element, placed))
    return rects, clipped, outside
