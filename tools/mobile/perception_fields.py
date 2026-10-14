"""The geometry and drop-count fields of a pruned screen packet."""

from __future__ import annotations


# The DISPLAY. Stored because the tallest element bottom is NOT
# the viewport: a scroll container reports its CONTENT height, and
# deriving the device size from a max() walk shrank every element on
# every scrollable screen. `[]` when the dump carried no positive-area
# window at all, so the key means exactly one thing and a reader can
# treat an empty value and an older build's missing value alike.
# The FRAME, under its OWN name because it answers a different
# question: `root_bounds` is the display, and this is the region an
# element may occupy -- at least as large as everything the dump
# lays out. Stored because the report needs a scale even when the
# panel is unknown, and the alternative was `report._device_size`
# improvising one from a max() walk over the elements, which returns
# a scroll container's CONTENT height: the one value that whole
# function documents as the thing the scale must never be. Two
# rectangles, two names, one meaning each.
#
# From `frame`, NOT from a node's own box -- storing that would put
# a node's box under a name that promises the frame, which is the
# one-name-two-meanings defect this key exists to end.
def _geometry_fields(selected_frame: tuple, panel_axes: tuple, drops: tuple) -> dict:
    """The display, frame, per-class drop counts and per-axis panel of a screen."""
    selected, frame = selected_frame
    return {
        "root_bounds": list(selected) if selected else [],
        "frame_bounds": list(frame) if frame else [],
        **_panel_fields(drops, panel_axes),
    }


# WHICH class lost elements, because "truncated" alone cannot say
# whether the tester's own screen was thinned or an adjacent pager
# page was. A model told only that something was cut plans against a
# screen it believes is complete.
#
# NAMED FOR THE PANEL, NOT FOR THE TESTER'S EYES, because the panel
# can be wrong and these inherit its error. Under a stale display a
# control the tester is looking at falls OUTSIDE the panel, so
# `dropped_outside_panel` would have been read as "only an adjacent
# page was thinned" when the opposite is true. "Inside"/"outside"
# says what was actually measured -- which side of a rectangle -- and
# `root_bounds == []` tells a reader that rectangle was never known.
# A count named after what the tester can SEE would be presenting
# the panel's guess as provenance, which is the confident wrongness
# the rest of prune exists to avoid.
# No panel at all: not attributable to either side.
# The panel PER AXIS, `0` for an axis its own rule could not answer.
# `root_bounds` above is the same panel when BOTH axes are known and
# `[]` otherwise, which is what the report needs -- a scale must be a
# whole rectangle or absent. The gesture clamp needs the other shape:
# it can bind one axis while leaving the other alone, and collapsing
# that into the rectangle is what cost a real clamp on a window laid
# out above the display. Two consumers, two values, one meaning each.
def _panel_fields(drops: tuple, panel_axes: tuple) -> dict:
    """Per-class drop counts and the per-axis panel of a screen."""
    dropped_inside, dropped_outside, dropped_unclassified = drops
    return {
        "dropped_inside_panel": dropped_inside,
        "dropped_outside_panel": dropped_outside,
        "dropped_unclassified": dropped_unclassified,
        "panel_axes": list(panel_axes),
    }
