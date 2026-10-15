"""Replay: which control an OPTIONAL ``tap_text_if_present`` means when its word is on several.

``tap_text`` refuses a word on several controls (the model re-targets). An
optional tap has no model to hand back to -- it never fails the script -- so
here one candidate is chosen only when it is clearly right on ANY app:

1. the clickable controls, if any of them is;
2. those not covered by a LATER-drawn element that wholly contains them (the
   dialog or sheet on top hides the screen under it);
3. the one whose own words equal the text, over those that merely contain it.

Still several: ``ambiguous`` stays True and the caller skips with a warning.
"""

from __future__ import annotations

from typing import Any

from tools.mobile.replay.elements_hit import resolve_tap_text, tap_text_controls
from tools.mobile.replay.elements_label import _int_box


def _area(box: tuple) -> int:
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def _holds(outer: tuple, inner: tuple) -> bool:
    return (
        outer[0] <= inner[0]
        and outer[1] <= inner[1]
        and outer[2] >= inner[2]
        and outer[3] >= inner[3]
        and _area(outer) > _area(inner)
    )


def _covered(control: dict, elements: list) -> bool:
    """True when an element drawn AFTER *control* wholly contains it."""
    inner = _int_box(control.get("bounds"))
    here = [i for i, e in enumerate(elements) if e is control or e == control]
    if inner is None or not here:
        return False
    for later in elements[here[0] + 1 :]:
        outer = _int_box(later.get("bounds")) if isinstance(later, dict) else None
        if outer is not None and _holds(outer, inner):
            return True
    return False


def _says(control: dict, wanted: str, elements: list) -> bool:
    """True when the control's own words, or a child's inside it, equal *wanted*."""
    box = _int_box(control.get("bounds"))
    for element in [control, *elements]:
        words = {str(element.get(k) or "").strip().lower() for k in ("text", "desc")}
        inside = element is control or _holds(box or (0, 0, -1, -1), _box_of(element))
        if wanted in words and inside:
            return True
    return False


def _box_of(element: dict) -> tuple:
    return _int_box(element.get("bounds")) or (1, 1, 0, 0)


def _narrow(controls: list, keep: Any) -> list:
    """The controls *keep* accepts, or all of them when it accepts none."""
    kept = [c for c in controls if keep(c)]
    return kept or controls


def sole_control(controls: list, wanted: str, elements: list) -> dict | None:
    """The one clearly-right control among *controls*, or ``None``."""
    pool = _narrow(controls, lambda c: bool(c.get("clickable")))
    pool = _narrow(pool, lambda c: not _covered(c, elements))
    if len(pool) > 1:
        pool = _narrow(pool, lambda c: _says(c, wanted.lower(), elements))
    return pool[0] if len(pool) == 1 else None


def resolve_optional_text(text: Any, screen: Any) -> dict:
    """:func:`resolve_tap_text` for an optional tap: a word on several controls
    is narrowed to the clearly-right one, else it stays ``ambiguous``."""
    picked = resolve_tap_text(text, screen)
    if not picked["ambiguous"]:
        return picked
    controls, elements = tap_text_controls(text, screen)
    sole = sole_control(controls, str(text).strip(), elements)
    if sole is None:
        return picked
    return {"element": sole, "candidates": 1, "ambiguous": False}
