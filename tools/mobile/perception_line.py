"""One pruned element as a compact line, and the row and id helpers."""

from __future__ import annotations


# One pruned element as a single compact line.
#
# ``refs`` maps a FIELD NAME to the id of the element that already printed
# that field's value, and only :func:`render_lines` passes it. With the
# default ``None`` this function is byte-for-byte what it always was, which is
# what lets the pins that grade it keep grading it.
#
# THE SUBSTITUTION HAPPENS ON THE PART, NEVER ON THE FINISHED LINE. Replacing
# ``desc="V"`` in the joined string finds the FIRST occurrence of that
# substring, and no attribute is escaped -- so an element whose *text* value
# happens to read ``desc="V"`` had its TEXT rewritten to ``desc=@e1`` while
# the duplicate desc was still printed in full. The model was handed a value
# the app never displayed, on exactly the kind of screen this feature targets:
# a chat carrying markup. Found by EXECUTING, in the adversarial review of the
# commit that introduced it.
def element_line(element: dict, refs: dict | None = None) -> str:
    """One pruned element as a single compact line."""
    pointers = refs if isinstance(refs, dict) else {}
    return " ".join(_head_parts(element, pointers) + _tail_parts(element))


def _field_part(name: str, value: str, pointers: dict) -> str:
    """``name="value"``, or ``name=@id`` when an earlier element printed it."""
    return (
        name + "=@" + pointers[name] if name in pointers else name + '="' + value + '"'
    )


def _head_parts(element: dict, pointers: dict) -> list[str]:
    """Id, class, text, desc and rid."""
    parts = [str(element.get("id") or "?"), str(element.get("cls") or "?")]
    text = str(element.get("text") or "")
    desc = str(element.get("desc") or "")
    rid = str(element.get("rid") or "")
    if text:
        parts.append(_field_part("text", text, pointers))
    if desc and desc != text:
        parts.append(_field_part("desc", desc, pointers))
    if rid:
        parts.append("rid=" + rid)
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
    """Label, role, bounds and the boolean flags."""
    parts = _label_parts(element)
    role = str(element.get("role") or "")
    if role:
        parts.append("role=" + role)
    bounds = element.get("bounds") or []
    if len(bounds) == 4:
        parts.append("at[" + ",".join(str(int(v)) for v in bounds) + "]")
    parts.extend(
        name
        for name in ("clickable", "editable", "checked", "scrollable")
        if element.get(name)
    )
    return parts


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
