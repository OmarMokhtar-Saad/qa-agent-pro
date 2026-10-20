"""Content a scrollable container holds below the visible area, as one line."""

from __future__ import annotations

from tools.mobile.perception_bounds import parse_bounds
from tools.mobile.perception_line import _rows
from tools.mobile.perception_nodes import _node_element


def more_below(root: object, frame: tuple | None) -> int:
    """How many real elements sit fully below *frame*, which the tree drops."""
    if frame is None:
        return 0
    count = 0
    for node in root.iter("node"):  # type: ignore[attr-defined]
        bounds = parse_bounds(node.get("bounds"))
        if bounds is None or bounds[1] < frame[3] or bounds[3] <= bounds[1]:
            continue
        count += _node_element(node, bounds) is not None
    return count


def more_below_line(content: dict, shown: int) -> list[str]:
    """``… N more below (scroll)`` when a shown element scrolls and rows were clipped."""
    count = int(content.get("more_below") or 0)
    rows = _rows(content.get("elements"))[:shown]
    scrolls = any(element.get("scrollable") for element in rows)
    return ["… " + str(count) + " more below (scroll)"] if count and scrolls else []
