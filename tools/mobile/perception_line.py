"""One pruned element as a compact line, and the row and id helpers."""

from __future__ import annotations

from typing import NamedTuple

from tools.mobile.perception_kind import IMPLIED_FLAGS, kind_of


class Slot(NamedTuple):
    """Where an element sits in the shown list, and how its rid is printed."""

    n: int = 0
    full_rid: bool = False


# One pruned element as a single compact line:
# ``#n kind "text" (desc) id=short_rid [label=".."] [role=..] [bounds] [flags]``.
#
# ``refs`` maps a FIELD NAME (text / desc) to the ``#n`` of the element that
# already printed that field's value, and only :func:`render_lines` passes it.
# *slot* is the element's place in the shown list; without one the ``#n`` is
# left off and the line is the bare per-element form.
#
# THE SUBSTITUTION HAPPENS ON THE PART, NEVER ON THE FINISHED LINE. Replacing
# a value in the joined string finds the FIRST occurrence of that substring,
# and no attribute is escaped -- so an element whose *text* happens to read
# like a desc had its TEXT rewritten while the duplicate was still printed in
# full. Found by EXECUTING, in the adversarial review of the commit that
# introduced the dedupe.
def element_line(
    element: dict, refs: dict | None = None, slot: Slot | None = None
) -> str:
    """One pruned element as a single compact line."""
    pointers = refs if isinstance(refs, dict) else {}
    where = slot or Slot()
    head = ["#" + str(where.n)] if where.n else []
    parts = head + [kind_of(element)] + _head_parts(element, pointers, where)
    line = " ".join(parts + _tail_parts(element))
    return line if len(line) <= LINE_CAP else _shrunk_line(element, pointers, where)


def _shrunk_line(element: dict, pointers: dict, slot: Slot) -> str:
    """The line again with text, desc and label cut, flags and role kept."""
    cut = {
        key: capped(str(element.get(key) or "")) for key in ("text", "desc", "label")
    }
    shrunk = {**element, **cut}
    parts = ["#" + str(slot.n)] if slot.n else []
    parts += [kind_of(shrunk)] + _head_parts(shrunk, pointers, slot)
    return " ".join(parts + _tail_parts(shrunk))


#: A whole list line longer than this is shrunk by cutting its long fields.
LINE_CAP = 400

#: Most characters of one text, desc or label kept when a line is shrunk.
LINE_FIELD_CAP = 120


def capped(value: str) -> str:
    """*value* cut to :data:`LINE_FIELD_CAP` characters, ``…`` marking the cut."""
    if len(value) <= LINE_FIELD_CAP:
        return value
    return value[: LINE_FIELD_CAP - 1] + "…"


def field_text(name: str, value: str) -> str:
    """How a field value is printed in full: ``"text"`` or ``(desc)``."""
    return '"' + value + '"' if name == "text" else "(" + value + ")"


def _field_part(name: str, value: str, pointers: dict) -> str:
    """The value in full, or ``@#n`` when an earlier element printed it."""
    if name not in pointers:
        return field_text(name, value)
    return "@" + pointers[name] if name == "text" else "(@" + pointers[name] + ")"


def short_rid(rid: str) -> str:
    """The part of a resource id after ``/``."""
    return rid.rsplit("/", 1)[-1]


def _head_parts(element: dict, pointers: dict, slot: Slot) -> list[str]:
    """Text, desc and rid."""
    text = str(element.get("text") or "")
    desc = str(element.get("desc") or "")
    rid = str(element.get("rid") or "")
    parts = [_field_part("text", text, pointers)] if text else []
    if desc and desc != text:
        parts.append(_field_part("desc", desc, pointers))
    if rid:
        parts.append("id=" + (rid if slot.full_rid else short_rid(rid)))
    return parts


# Only when it adds something: for a labelled control the label IS the
# desc, and printing it twice spends the packet's budget on nothing.
def _label_parts(element: dict) -> list[str]:
    """``label="..."`` when the label differs from both text and desc."""
    text = str(element.get("text") or "")
    desc = str(element.get("desc") or "")
    label = str(element.get("label") or "")
    return ['label="' + label + '"'] if label and label not in (text, desc) else []


def _tail_parts(element: dict) -> list[str]:
    """Label, role, bounds (only when nothing else names it) and the flags."""
    parts = _label_parts(element)
    role = str(element.get("role") or "")
    if role:
        parts.append("role=" + role)
    bounds = element.get("bounds") or []
    nameless = not (element.get("text") or element.get("desc") or element.get("rid"))
    if len(bounds) == 4 and nameless:
        parts.append("at[" + ",".join(str(int(v)) for v in bounds) + "]")
    return parts + _flag_parts(element)


def _flag_parts(element: dict) -> list[str]:
    """The true flags the kind does not already imply."""
    implied = IMPLIED_FLAGS.get(kind_of(element), ())
    return [
        name
        for name in ("clickable", "editable", "checked", "scrollable")
        if element.get(name) and name not in implied
    ]


# The element list as dicts, or ``[]`` -- never an exception.
#
# ONE coercion, called by both consumers. ``list(elements or [])`` was written
# at each of them and raises TypeError on an int, and a stored packet is an
# outside input by the time it is read: the caller's ``except`` would discard
# the ENTIRE screen block over a malformed field, which is the shape this
# file's history is made of (see :func:`_count`). Two sites deriving one
# coercion is also the mirrored-condition defect CLAUDE.md names -- they
# drift, and the second one's fall-through is silent. Found by EXECUTING the
# parametrised malformed-input test, not by reading it.
def _rows(elements: object) -> list[dict]:
    """The element list as dicts, or ``[]`` -- never an exception."""
    if not isinstance(elements, (list, tuple)):
        return []
    return [element for element in elements if isinstance(element, dict)]


# The id string the summaries PRINT. ONE expression, every caller.
#
# For DISPLAY only. It is emphatically not a key: `prune` guarantees unique
# non-empty ids but a STORED packet does not, and two controls with no id
# collide on one entry -- which is how both of them came to read ``Voice
# mode``, the exact incident these summaries exist to prevent. Per-element
# values are keyed by object identity instead; see :func:`_summary_labels`.
def _eid(element: object) -> str:
    """The id string the summaries PRINT."""
    body = element if isinstance(element, dict) else {}
    return str(body.get("id") or "")
