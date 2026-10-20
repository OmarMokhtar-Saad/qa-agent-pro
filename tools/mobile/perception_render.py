"""Every element as a line, with each long repeated string printed once."""

from __future__ import annotations

from collections import Counter

from tools.mobile.perception_limits import MIN_DEDUPE_CHARS
from tools.mobile.perception_line import (
    Slot,
    _rows,
    element_line,
    field_text,
    short_rid,
)


# Every element as a line, with each LONG repeated string printed once.
#
# :func:`element_line` dedupes ``text`` / ``desc`` / ``label`` WITHIN one node.
# It cannot see across the list, and a chat screen is where that costs: one
# 200-character reply was printed as a parent's ``label``, a child's ``desc``,
# a grandchild's ``text`` and again in the controls summary -- four copies of
# one string per bubble, and every prior turn's bubbles persist.
#
# ``element_line`` is deliberately left pure: it is a per-element function test
# modules pin directly, and the cross-element pass is a different question
# about a different input. This is the one producer of the rendered list.
#
# * A repeat points at the ``#n`` of the line that printed it in full
#   (``@#3``), and only ever at an EARLIER line, so a trim from the tail can
#   never orphan a reference.
# * ``label=`` is never REPLACED, only used as an anchor: a label is a
#   targeting selector and an abbreviated one costs a turn every time.
# * Short strings are left alone (:data:`MIN_DEDUPE_CHARS`).
def render_lines(elements: object) -> list[str]:
    """Every element as a line, with each LONG repeated string printed once."""
    rows = _rows(elements)
    anchors: dict = {}
    out: list = []
    for element, slot in zip(rows, slots_of(rows), strict=True):
        row = (element_line(element, None, slot), "#" + str(slot.n))
        refs = _field_refs(element, row, anchors)
        _anchor_label(element, row, anchors)
        out.append(element_line(element, refs, slot) if refs else row[0])
    return out


def plain_lines(elements: object) -> list[str]:
    """Every element as a line with no references."""
    rows = _rows(elements)
    slots = slots_of(rows)
    return [element_line(e, None, s) for e, s in zip(rows, slots, strict=True)]


def slots_of(rows: list) -> list[Slot]:
    """The 1-based position of each row, and whether its short rid collides."""
    shorts = [short_rid(str(e.get("rid") or "")) for e in rows]
    counts = Counter(s for s in shorts if s)
    return [Slot(i + 1, bool(s) and counts[s] > 1) for i, s in enumerate(shorts)]


# Already suppressed by the within-node rule, or truncated to something this
# element does not literally print: either way there is nothing to replace,
# and replacing blind is how a reference ends up pointing at a string nobody
# printed. A repeat is recorded as a FIELD to point at, and rendered by
# `element_line` on the part itself, never by a replace on the finished line.
def _field_refs(element: dict, row: tuple, anchors: dict) -> dict:
    """The ``text`` / ``desc`` fields to print as references; records new anchors."""
    plain, ref = row
    refs: dict = {}
    for field in ("text", "desc"):
        value = str(element.get(field) or "")
        if len(value) < MIN_DEDUPE_CHARS or field_text(field, value) not in plain:
            continue
        seen = anchors.get(value)
        if seen and seen != ref:
            refs[field] = seen
        else:
            anchors.setdefault(value, ref)
    return refs


def _anchor_label(element: dict, row: tuple, anchors: dict) -> None:
    """Record a long ``label`` as an anchor; it is never itself replaced."""
    plain, ref = row
    label = str(element.get("label") or "")
    if len(label) >= MIN_DEDUPE_CHARS and ('label="' + label + '"') in plain:
        anchors.setdefault(label, ref)
