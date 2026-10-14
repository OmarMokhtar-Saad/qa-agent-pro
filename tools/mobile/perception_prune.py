"""``prune``: the untrusted dump -> the ``{"error", "content"}`` packet."""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from tools.mobile.perception_content import _audit, _cap_and_label, _screen_content
from tools.mobile.perception_display import _display_rect
from tools.mobile.perception_fields import _geometry_fields
from tools.mobile.perception_nodes import _collect_elements, _parse_dump

logger = logging.getLogger("tools.mobile.perception")


class ScreenView(NamedTuple):
    """The device's display rectangle and density, when known."""

    display: object = None
    density: object = None


# The DISPLAY -- from the device where the caller had it, and from the
# extent of the dump's own windows where it did not. Never inferred from
# which window "looks like" the display: that question has no answer in
# a dump, and four rounds of asking it are in docs/DECISIONS.md.
# TWO rectangles, because the two readers fail in opposite directions.
# `selected` is the display and becomes the report's scale; `frame` is
# the display widened to cover everything the dump lays out, and is the
# visibility filter, because a filter smaller than the dump deletes the
# tester's screen in silence. See `_display_rect`.
def _pruned_content(root: Any, request: tuple) -> dict:
    """The screen packet for a parsed dump; *request* = (activity, display, density)."""
    activity, display, density = request
    selected, frame, panel_axes = _display_rect(root, display, root.get("rotation"))
    elements, texts, package, considered = _collect_elements(root, frame)
    elements, truncated, drops = _cap_and_label(elements, selected)
    geometry = _geometry_fields((selected, frame), panel_axes, drops)
    extras = {**geometry, "truncated": truncated, "considered": considered}
    content = _screen_content(elements, (texts, package, activity), extras)
    content["accessibility"] = _audit(content, density)
    return content


# Returns the ``{"error", "content"}`` shape every module in this package
# returns, rather than the bare dict the programme spec sketched: a refusal
# here (hostile dump, oversize, unparseable) is information a handler must
# render, and a bare dict has nowhere to put it.
def prune(xml: object, activity: str = "", view: ScreenView | None = None) -> dict:
    """Untrusted dump -> ``{"screen_id", "elements", "hash", "package", ...}``."""
    try:
        root: Any
        root, refusal = _parse_dump(xml)
        if refusal:
            return {"error": refusal, "content": None}
        request = (activity, *(view or ScreenView()))
        return {"error": None, "content": _pruned_content(root, request)}
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.perception.prune failed")
        return {"error": str(exc), "content": None}
