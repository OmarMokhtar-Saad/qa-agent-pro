"""The wireframe box ceiling, the frame scale and the device-to-frame rect scaling."""

from __future__ import annotations

from typing import Any

#: The CEILING the rendered wireframe box may not exceed, in CSS px. REVERSED
#: from a fixed 360x800 box (docs/DECISIONS.md -> "The frame takes the
#: device's own aspect"): a fixed frame let a landscape phone draw at a fifth
#: of the available area inside a tall empty box. `_frame_box` now fits the
#: device's own aspect INSIDE this ceiling, so a full-screen element always
#: fills its frame.
MAX_FRAME_W = 360


MAX_FRAME_H = 800


# One producer, because two consumers need the SAME float and not merely the
# same formula. :func:`_frame_box` multiplies it to size the box;
# :func:`scale_bounds` multiplies it to place every rect inside that box. When
# `scale_bounds` re-derived its own scale from the already-rounded box, the
# box's integer aspect differed from the device's by a fraction of a pixel and
# a full-screen element came out one pixel short of the frame it fills -- the
# same input, derived twice, drifting. Callers must guarantee both arguments
# are positive; `_frame_box`'s guard is what establishes that.
def _frame_scale(dev_w: int, dev_h: int) -> float:
    """THE scale factor for one device: device pixels -> frame pixels."""
    return min(MAX_FRAME_W / float(dev_w), MAX_FRAME_H / float(dev_h))


# The rendered wireframe box for one device: its own aspect, contained
# inside the ``MAX_FRAME_W``x``MAX_FRAME_H`` ceiling.
#
# ONE producer of frame size -- :func:`scale_bounds` and :func:`wireframe`
# both call this rather than each deriving it, so the clamp and the drawn
# box can never disagree (the two-derivations trap this project has hit
# before). Guarantees: both returned values are ``int``, ``>= 1``, and
# ``<= `` their respective ceiling. A non-positive device (the guard this
# work owns -- see the mutation clause ratchet) falls back to the full
# ceiling box rather than dividing by zero or returning a degenerate one.
def _frame_box(dev_w: int, dev_h: int) -> tuple:
    """The rendered wireframe box for one device: its own aspect inside the ceiling."""
    if dev_w <= 0 or dev_h <= 0:
        return (MAX_FRAME_W, MAX_FRAME_H)
    scale = _frame_scale(dev_w, dev_h)
    # TRUNCATE, exactly as `scale_bounds` does for w/h. The two must round the
    # same way or a full-device element lands a pixel short of its own frame:
    # round() here gave a 2208px-tall device a 450px box whose full-screen rect
    # scaled to 449. Same input, two derivations, one drifting -- the trap this
    # producer exists to close, reintroduced by the arithmetic rather than by a
    # second call site. Truncation is also the safe direction: the box can only
    # be smaller than the exact fit, never larger, so nothing paints outside it.
    frame_w = min(MAX_FRAME_W, max(1, int(dev_w * scale)))
    frame_h = min(MAX_FRAME_H, max(1, int(dev_h * scale)))
    return (frame_w, frame_h)


# Guarantees, unconditionally, for every tuple it returns: all four values are
# ``int``, ``x >= 0``, ``y >= 0``, ``x + w <= frame_w`` and ``y + h <=
# frame_h``, where ``frame_w, frame_h = _frame_box(dev_w, dev_h)`` and so
# ``frame_w <= MAX_FRAME_W`` and ``frame_h <= MAX_FRAME_H`` always. A
# malformed dump must not be able to paint outside the frame or raise.
# THE guard. A zero or negative device collapses every clamped box to
# right <= left (or bottom <= top), so this one check is what refuses
# it -- and it is the only reachable one. Two explicit zero-device
# checks (here and in wireframe) sat in front of it and both graded
# dead under mutation (round-14): removing either changed nothing.
# The division below is reached only when right > left, which needs
# dev_w > 0.
def scale_bounds(box: Any, dev_w: int, dev_h: int) -> tuple | None:
    """Device pixels -> a rect inside this device's own frame, or None."""
    try:
        left, top, right, bottom = (int(value) for value in box)
        left = min(max(0, left), int(dev_w))
        right = min(max(0, right), int(dev_w))
        top = min(max(0, top), int(dev_h))
        bottom = min(max(0, bottom), int(dev_h))
        if right <= left or bottom <= top:
            return None
        return _scaled_rect((left, top, right, bottom), dev_w, dev_h)
    except (TypeError, ValueError, OverflowError):
        return None


# The SAME float `_frame_box` sized the box with -- never a scale
# re-derived from the rounded box, which drifts by a pixel on the axis
# the ceiling did not constrain.
def _scaled_rect(rect: tuple, dev_w: int, dev_h: int) -> tuple:
    """The clamped device rect scaled into this device's own frame."""
    left, top, right, bottom = rect
    frame_w, frame_h = _frame_box(dev_w, dev_h)
    scale = _frame_scale(dev_w, dev_h)
    x = int(left * scale)
    y = int(top * scale)
    w = max(1, int((right - left) * scale))
    h = max(1, int((bottom - top) * scale))
    x = min(x, frame_w - 1)
    y = min(y, frame_h - 1)
    w = min(w, frame_w - x)
    h = min(h, frame_h - y)
    return (x, y, w, h)
