"""The two layers ``fill`` tries for a label: the field's own strings, then the nearest field."""

from __future__ import annotations

from typing import Any

from tools.mobile.fill_label_geom import _claimed_between, _distance, _Pair, _rect
from tools.mobile.fill_label_match import _matches, _Query, _strings
from tools.mobile.fill_label_owner import owning_fields

LAYER_FIELD = "field"
LAYER_CONTAINED = "contained"
LAYER_ADJACENT = "adjacent"
LAYERS = (LAYER_FIELD, LAYER_CONTAINED, LAYER_ADJACENT)


def _field_layer(elements: list, label: str, tier: str) -> list:
    query = _Query(label, tier)
    return [
        e
        for e in elements
        if e.get("editable") and _matches(e, ("hint", "text", "desc"), query)
    ]


def _boxed_fields(elements: list) -> list:
    fields: list[Any] = [(e, _rect(e)) for e in elements if e.get("editable")]
    return [(e, box) for e, box in fields if box]


def _nearest(box: tuple, fields: list, other_boxes: list) -> list:
    tol = max(4, (box[3] - box[1]) // 4)
    scored = []
    for field, f_box in fields:
        gap = _distance(box, f_box, tol)
        if gap is None:
            continue
        if _claimed_between(_Pair(box, f_box, tol), other_boxes):
            continue
        scored.append((gap, field))
    if not scored:
        return []
    nearest = min(gap for gap, _ in scored)
    return [f for gap, f in scored if gap == nearest]


def _adjacent_layer(elements: list, label: str, tier: str) -> list:
    texts = [
        e for e in elements if not e.get("editable") and _strings(e, ("text", "desc"))
    ]
    query = _Query(label, tier)
    labels = [e for e in texts if _matches(e, ("text", "desc"), query)]
    owned = owning_fields(elements)
    fields = [fb for fb in _boxed_fields(elements) if fb[0] not in owned]
    other_boxes = [_rect(e) for e in texts if e not in labels]
    other_boxes = [box for box in other_boxes if box]
    winners: list = []
    for node in labels:
        box = _rect(node)
        if not box:
            continue
        winners.extend(
            f for f in _nearest(box, fields, other_boxes) if f not in winners
        )
    return winners
