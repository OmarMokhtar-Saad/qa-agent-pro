"""One wireframe on the report page, or the honest empty frame for a missing look."""

from __future__ import annotations

from tools.mobile.report_text import esc
from tools.mobile.rfrm_look import (
    AMBIGUOUS_LOOK,
    LOOK_AMBIGUOUS,
    LOOK_UNCAPTURED,
    NOT_CAPTURED,
    _resolve_look,
)
from tools.mobile.rfrm_wire import wireframe


# THE INVARIANT, DECIDED IN `_resolve_look` AND HONOURED HERE.
#
# A bare screen id says WHICH screen. It cannot say WHAT was on it: the
# screen library holds the LAST look, and a screen observed more than
# once has several. Resolving a frame against it draws the newest
# content under whatever caption the caller wrote -- on a conversation,
# a reply the app had not yet produced, shown to a non-technical tester
# as evidence of what their action did.
#
# This is the THIRD consumer to make that mistake: the sequence row's
# "Screen after", the overview's opening-screen count, and the case
# card's "Last screen" -- each found in a separate review round, each
# patched separately, and two of them introduced by the change that
# created the second library in the first place. So the rule stops being
# something every caller must remember and becomes something no caller
# can get wrong.
#
# Ambiguous means TWO OR MORE stored looks. Exactly one is not
# ambiguous -- the bare id and that single observation are the same
# bytes -- so a settings walk and every single-look screen render as
# they always did. That is what keeps this a guard and not a regression.
def _frame_html(screen_id: object, screens: object) -> str:
    """One frame, or an honest empty one when the screen was not stored."""
    ident, screen, verdict = _resolve_look(screen_id, screens)
    if not ident:
        return (
            '<div class="frame missing"><p class="wirenote">'
            + esc(NOT_CAPTURED)
            + "</p></div>"
        )
    if verdict == LOOK_UNCAPTURED:
        return _missing_frame(ident, NOT_CAPTURED)
    if verdict == LOOK_AMBIGUOUS:
        return _missing_frame(ident, AMBIGUOUS_LOOK)
    return _drawn_frame(ident, wireframe(screen))


def _missing_frame(ident: str, note: str) -> str:
    """An empty frame for *ident* carrying *note* as its reason."""
    return (
        '<div class="frame missing" data-screen="'
        + esc(ident, 40)
        + '"><p class="wirenote">'
        + esc(note)
        + "</p></div>"
    )


def _rect_html(rect: dict) -> str:
    """One element rectangle of the wireframe."""
    return (
        '<div class="rect '
        + esc(rect["kind"], 12)
        + '" style="left:'
        + str(int(rect["x"]))
        + "px;top:"
        + str(int(rect["y"]))
        + "px;width:"
        + str(int(rect["w"]))
        + "px;height:"
        + str(int(rect["h"]))
        + 'px" title="'
        + esc(rect["label"], 40)
        + '" dir="auto">'
        + esc(rect["label"], 40)
        + "</div>"
    )


def _drawn_frame(ident: str, frame: dict) -> str:
    """The wireframe of one stored screen: its rectangles and geometry note."""
    rects = "".join(_rect_html(rect) for rect in frame["rects"])
    return (
        '<div class="frame" style="width:'
        + str(int(frame["frame_w"]))
        + "px;height:"
        + str(int(frame["frame_h"]))
        + 'px"'
        + _frame_data(ident, frame)
        + rects
        + _geometry_note(frame)
        + "</div>"
    )


def _frame_data(ident: str, frame: dict) -> str:
    """The data-* attributes of a drawn frame, from a leading space to the tag's ``>``."""
    return (
        ' data-screen="'
        + esc(ident, 40)
        + '" data-rects="'
        + str(len(frame["rects"]))
        + '" data-scaled="'
        + ("1" if frame["scaled"] else "0")
        + '" data-clipped="'
        + str(int(frame.get("clipped") or 0))
        + '" data-outside="'
        + str(int(frame.get("outside") or 0))
        + '">'
    )


# Rendered as ``.frame .wirenote.geom`` -- the shell styles it (round-14
# review, H3: the first version emitted an unstyled class in-flow under the
# absolutely positioned rects, where nobody could read it) and pins it to the
# frame's foot above the rects. Both counts are ints this module computed, so
# the interpolation is the one kind that needs no escaping.
def _geometry_note(frame: dict) -> str:
    """The frame's disclosure of what the picture could not show, or ''."""
    clipped = int(frame.get("clipped") or 0)
    outside = int(frame.get("outside") or 0)
    if not clipped and not outside:
        return ""
    parts = []
    if clipped:
        parts.append(
            str(clipped) + " element(s) extend past the display and are drawn clipped"
        )
    if outside:
        parts.append(
            str(outside) + " element(s) lie entirely off the display and are not drawn"
        )
    return '<p class="wirenote geom">' + "; ".join(parts) + "</p>"
