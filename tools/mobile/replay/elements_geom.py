"""Replay element geometry: centres, display boxes and the on-screen clamp."""

from __future__ import annotations

from typing import Any


def _center(element: dict) -> tuple[int, int] | None:
    bounds = element.get("bounds") or []
    if len(bounds) != 4:
        return None
    try:
        x1, y1, x2, y2 = (int(v) for v in bounds)
    except (TypeError, ValueError, OverflowError):
        return None
    return (x1 + x2) // 2, (y1 + y2) // 2


def _display_box(display: Any) -> tuple | None:
    """*display* as ``(x1, y1, x2, y2)``, or ``None`` when it cannot be used.

    NEVER RAISES and never guesses. A packet with no ``root_bounds``, a
    malformed one, or a degenerate rectangle all return ``None``, which means
    "do not clamp the far edges" -- the pre-existing behaviour. Refusing to
    clamp is the safe direction here: clamping to a wrong or stale rectangle
    would shrink a legitimate gesture out of existence, which is the mistake
    the display work itself had to reverse one round earlier.
    """
    try:
        x1, y1, x2, y2 = (int(v) for v in display)  # type: ignore[misc]
    except (TypeError, ValueError, OverflowError):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2)


# The far edges the panel is known to have, as ``(x_far, y_far)``.
#
# Either may be ``None``, meaning "this axis is unknown, so do not clamp it" --
# the same safe direction :func:`_display_box` already takes for a whole
# missing rectangle, applied one axis at a time. Clamping to a guess is what
# shrinks a legitimate gesture out of existence; clamping to nothing is what
# let a swipe leave the panel. Per axis, neither is forced.
#
# Accepts BOTH shapes on purpose. Two numbers is the packet's ``panel_axes``,
# where ``0`` marks an axis the panel's own rule could not answer. Four is the
# older ``root_bounds``, which is all-or-nothing by construction, so a packet
# from a build that does not publish ``panel_axes`` clamps exactly as it does
# today. Anything else clamps nothing.
#
# Never raises: a stored packet is an outside input by the time it is read.
def _edge(value: Any) -> int | None:
    if isinstance(value, bool):
        # `int(True)` is a 1-pixel panel, which would clamp every
        # gesture to nothing -- the shrinking direction this must never
        # take.
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number > 0 else None


def _panel_far_edges(display: Any) -> tuple:
    """The far edges the panel is known to have, as ``(x_far, y_far)``."""
    if isinstance(display, (list, tuple)) and len(display) == 2:
        return (_edge(display[0]), _edge(display[1]))
    box = _display_box(display)
    if box is None:
        return (None, None)
    return (box[2], box[3])


def _clamp_source(screen: Any) -> Any:
    """What the clamp reads out of a packet: the per-axis panel, else the rect.

    Both call sites go through here so the two cannot disagree about which key
    wins -- deriving that answer twice in two places is how mirrored conditions
    drift, and this pair of call sites has already been found out of step once.
    """
    body = screen if isinstance(screen, dict) else {}
    axes = body.get("panel_axes")
    if isinstance(axes, (list, tuple)) and len(axes) == 2:
        return axes
    return body.get("root_bounds")


# The ON-SCREEN part of *element*'s bounds as ``(x1, y1, x2, y2)``.
# The ON-SCREEN part. Near edges always: a negative edge is where
# `adb._coord` refuses to put the finger. Far edges too WHEN the caller
# knows the display, because a partly-visible node keeps its far edges and
# a gesture built from them lands off the panel and silently no-ops.
# Everything below is computed from this box, so no later clamp can move an
# endpoint after the invariant has been checked.
# Near edges from the whole rectangle when there is one. A panel's origin
# is always (0, 0), so this only ever matters for a stored packet that
# carries something else.
# FAR edges PER AXIS. An axis the panel's own rule could not answer is left
# unclamped rather than clamped to a guess -- and before this, ONE
# unanswerable axis cost the clamp BOTH, so a window laid out above the
# display produced a swipe to x=2300 against a 1080 panel that the device
# silently ignored while the step reported success.
def _on_screen_box(element: dict, display: Any) -> tuple[int, int, int, int]:
    """The ON-SCREEN part of *element*'s bounds as ``(x1, y1, x2, y2)``."""
    x1, y1, x2, y2 = (int(v) for v in element["bounds"])
    x1, y1 = max(0, x1), max(0, y1)
    frame = _display_box(display)
    if frame is not None:
        x1, y1 = max(x1, frame[0]), max(y1, frame[1])
    x_far, y_far = _panel_far_edges(display)
    if x_far is not None:
        x2 = min(x2, x_far)
    if y_far is not None:
        y2 = min(y2, y_far)
    return x1, y1, x2, y2
