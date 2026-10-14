"""The ``contained`` layer: a Material floating label drawn INSIDE its own field.

``TextInputLayout`` draws its label as a separate ``TextView`` whose bounds lie
inside the ``EditText`` (which then has no hint, text or desc). Adjacency would
skip that field -- its top is above the label's bottom -- and pick the NEXT
field down. A field holding a label's centre owns that label instead.
"""

from __future__ import annotations

import re

from tools.mobile.fill_label_geom import _rect
from tools.mobile.fill_label_match import _matches, _Query, _strings

_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)
_OVERLAY = re.compile(r"\b(show|hide|clear|toggle|visibility|eye)\b", re.I)
_MIN_LETTERS = 2
_MIN_CHARS = 4


def _label_like(node: dict, field: dict) -> bool:
    """Text that can name a field: letters, not a prefix/unit or an overlay action."""
    own = {str(field.get(k) or "") for k in ("text", "hint", "desc")}
    return any(
        len(_LETTER.findall(t)) >= _MIN_LETTERS
        and len(t.strip()) >= _MIN_CHARS
        and t not in own
        and not _OVERLAY.search(t)
        for t in _strings(node, ("text", "desc"))
    )


def _holds(field_box: tuple, label_box: tuple) -> bool:
    cx = (label_box[0] + label_box[2]) // 2
    cy = (label_box[1] + label_box[3]) // 2
    return field_box[0] <= cx <= field_box[2] and field_box[1] <= cy <= field_box[3]


def _text_nodes(elements: list) -> list:
    return [
        e for e in elements if not e.get("editable") and _strings(e, ("text", "desc"))
    ]


def _boxed(elements: list, editable: bool) -> list:
    pairs = [(e, _rect(e)) for e in elements if bool(e.get("editable")) == editable]
    return [(e, box) for e, box in pairs if box]


def contained_layer(elements: list, label: str, tier: str) -> list:
    query = _Query(label, tier)
    labels = [e for e in _text_nodes(elements) if _matches(e, ("text", "desc"), query)]
    label_boxes = [box for _, box in _boxed(labels, False)]
    return [
        field
        for field, f_box in _boxed(elements, True)
        if any(_holds(f_box, box) for box in label_boxes)
    ]


def owning_fields(elements: list) -> list:
    """Fields that hold a label-like text node's centre -- they own a label."""
    nodes = _boxed(_text_nodes(elements), False)
    return [
        field
        for field, f_box in _boxed(elements, True)
        if any(_holds(f_box, box) and _label_like(n, field) for n, box in nodes)
    ]
