"""The geometry behind the ``adjacent`` layer: which field sits below or right of a label."""

from __future__ import annotations

from typing import NamedTuple


class _Pair(NamedTuple):
    label_box: tuple
    field_box: tuple
    tol: int


def _rect(element: dict) -> tuple | None:
    box = element.get("bounds")
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        left, top, right, bottom = (int(v) for v in box)
    except (TypeError, ValueError, OverflowError):
        return None
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _distance(label_box: tuple, field_box: tuple, tol: int) -> int | None:
    """Pixels from a label to a field below or right of it, else ``None``."""
    l_left, l_top, l_right, l_bottom = label_box
    f_left, f_top, f_right, f_bottom = field_box
    overlaps_rows = f_top < l_bottom and f_bottom > l_top
    if overlaps_rows and f_left >= l_right - tol:
        return max(0, f_left - l_right)
    overlaps_cols = f_left < l_right and f_right > l_left
    if overlaps_cols and f_top >= l_bottom - tol:
        return max(0, f_top - l_bottom)
    return None


def _claimed_between(pair: _Pair, others: list) -> bool:
    """True when another text node sits between a label and a field below it --
    the field then belongs to THAT text, not to this label."""
    label_box, field_box, tol = pair
    for box in others:
        inside_rows = box[1] >= label_box[3] - tol and box[3] <= field_box[1] + tol
        inside_cols = box[0] < field_box[2] and box[2] > field_box[0]
        if inside_rows and inside_cols and box != label_box:
            return True
    return False
