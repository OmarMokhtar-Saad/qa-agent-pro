"""Every element as a line, with each long repeated string printed once."""

from __future__ import annotations

from tools.mobile.perception_limits import MIN_DEDUPE_CHARS
from tools.mobile.perception_line import _rows, element_line


# Every element as a line, with each LONG repeated string printed once.
#
# :func:`element_line` dedupes ``text`` / ``desc`` / ``label`` WITHIN one node.
# It cannot see across the list, and a chat screen is where that costs: on the
# observed run mrun-20260905-051728-bd1777 (package
# an Arabic-language chat app) one 200-character Arabic reply was
# printed as a parent's ``label``, a child's ``desc``, a grandchild's ``text``
# and again in the controls summary -- four copies of one string per bubble,
# and every prior turn's bubbles persist, so the block grew with the length of
# the conversation.
#
# ``element_line`` is deliberately left ALONE and pure: it is a per-element
# function two test modules pin directly, and the cross-element pass is a
# different question about a different input. This function is the one
# producer of the rendered list; ``to_prompt_block`` and the display-invariant
# helpers both read it, so there is no second derivation to drift.
#
# The clauses, each graded by its own fixture in
# ``tests/mobile/screen_matrix.py``:
#
# * **The anchor is always EARLIER in this list than the reference.**
#   ``to_prompt_block`` trims from the END, so a backward-only reference can
#   never be orphaned by the trim. A dangling ``@e7`` is worse than the
#   repetition it saved, and "recompute the dedupe after every trim" is the
#   alternative that makes a quadratic loop cubic.
# * **``label=`` is never REPLACED, only ever used as an anchor.**
#   ``actions.SELECTORS`` makes a label a targeting selector and the system
#   prompt tells the model to prefer ``role`` or ``label``, so an abbreviated
#   label costs a turn every time it is sent back. It still anchors, because
#   the observed screen carried the sentence as a parent's label first and the
#   child's ``desc`` has to be able to point at it.
# * **Short strings are left alone** (:data:`MIN_DEDUPE_CHARS`).
#
# Every element keeps its ``id``, which is what makes a referenced element
# still targetable rather than merely still described.
def render_lines(elements: object) -> list[str]:
    """Every element as a line, with each LONG repeated string printed once."""
    anchors: dict = {}
    out: list = []
    for element in _rows(elements):
        plain = element_line(element)
        row = (plain, str(element.get("id") or ""))
        refs = _field_refs(element, row, anchors)
        _anchor_label(element, row, anchors)
        out.append(element_line(element, refs) if refs else plain)
    return out


# Already suppressed by the within-node rule, or truncated to something this
# element does not literally print: either way there is nothing to replace,
# and replacing blind is how a reference ends up pointing at a string nobody
# printed. A repeat is recorded as a FIELD to point at, and rendered by
# `element_line` on the part itself. The previous version did a `str.replace`
# on this finished line and could rewrite an occurrence sitting inside a
# DIFFERENT attribute's value.
def _field_refs(element: dict, row: tuple, anchors: dict) -> dict:
    """The ``text`` / ``desc`` fields to print as references; records new anchors."""
    plain, eid = row
    refs: dict = {}
    for field in ("text", "desc"):
        value = str(element.get(field) or "")
        if len(value) < MIN_DEDUPE_CHARS or (field + '="' + value + '"') not in plain:
            continue
        seen = anchors.get(value)
        if seen and seen != eid:
            refs[field] = seen
        elif eid:
            anchors.setdefault(value, eid)
    return refs


def _anchor_label(element: dict, row: tuple, anchors: dict) -> None:
    """Record a long ``label`` as an anchor; it is never itself replaced."""
    plain, eid = row
    label = str(element.get("label") or "")
    if len(label) >= MIN_DEDUPE_CHARS and eid and ('label="' + label + '"') in plain:
        anchors.setdefault(label, eid)
