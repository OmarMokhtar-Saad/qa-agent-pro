"""The bounded screen summary a step reply carries.

Ninth layer of the ``render_*`` split of ``render.py``.

``compact_summary`` -- a short, bounded description of a pruned screen, or
``""`` when it has no elements. A header line plus at most
``MAX_SUMMARY_LINES`` element lines. Whole lines are dropped from the end to
stay within ``MAX_SUMMARY_CHARS``; a line is never cut in the middle. Never
raises.

``screen_block`` -- ``compact_summary`` as an untrusted block: the text comes
from the app on the device, so it is never an instruction.
"""

from __future__ import annotations

from tools.mobile import perception
from tools.mobile.render_field import _field
from tools.untrusted import wrap_untrusted

#: The screen summary a step reply carries: element lines shown, total
#: characters, and characters of any one device-supplied value in its header.
MAX_SUMMARY_LINES = 8
MAX_SUMMARY_CHARS = 600
MAX_SUMMARY_FIELD_CHARS = 60


def _summary_text(value: object) -> str:
    return _field(value)[:MAX_SUMMARY_FIELD_CHARS]


def _focused_part(elements: list) -> list[str]:
    focused = next((e for e in elements if e.get("focused")), None)
    if not focused:
        return []
    name = (
        focused.get("label")
        or focused.get("text")
        or focused.get("desc")
        or focused.get("rid")
        or focused.get("id")
    )
    return ["focused " + _summary_text(name)]


def _changed_part(body: dict, previous_id: object) -> list[str]:
    screen_id = str(body.get("screen_id") or "")
    if screen_id and previous_id:
        return ["changed" if screen_id != str(previous_id) else "unchanged"]
    return []


def _summary_header(body: dict, elements: list, previous_id: object) -> str:
    """The one header line: app, title, element count, focus, changed or not."""
    parts = []
    package = _summary_text(body.get("package"))
    title = _summary_text(body.get("title") or body.get("activity"))
    if package:
        parts.append(package)
    if title:
        parts.append(title)
    parts.append(str(len(elements)) + " elements")
    parts += _focused_part(elements)
    parts += _changed_part(body, previous_id)
    return "Screen: " + ", ".join(parts)


def _summary_line(element: dict) -> str:
    try:
        return " ".join(perception.element_line(element).split())
    except (TypeError, ValueError, AttributeError):
        return ""


#: A short, bounded description of a pruned screen, or ``""`` when it has no
#: elements: a header line plus at most ``MAX_SUMMARY_LINES`` element lines.
#: Whole lines are dropped from the end to stay within ``MAX_SUMMARY_CHARS``; a
#: line is never cut in the middle. Never raises.
def compact_summary(screen: object, previous_id: object = "") -> str:
    """A short, bounded description of a pruned screen. Never raises."""
    body = screen if isinstance(screen, dict) else {}
    raw = body.get("elements")
    elements = [
        e for e in (raw if isinstance(raw, list) else []) if isinstance(e, dict)
    ]
    if not elements:
        return ""
    lines = [_summary_header(body, elements, previous_id)]
    screen_id = str(body.get("screen_id") or "")
    if screen_id and previous_id and screen_id == str(previous_id):
        # The step ended on the screen it began on: as with the elided
        # dump-first packet, the elements are not repeated.
        return lines[0]
    shown = (_summary_line(e) for e in elements[:MAX_SUMMARY_LINES])
    lines += [line for line in shown if line]
    while len(lines) > 1 and len("\n".join(lines)) > MAX_SUMMARY_CHARS:
        lines.pop()
    return "\n".join(lines)[:MAX_SUMMARY_CHARS]


def screen_block(screen: object, previous_id: object = "") -> str:
    """``compact_summary`` as an untrusted block, never an instruction."""
    summary = compact_summary(screen, previous_id)
    if not summary:
        return ""
    return wrap_untrusted("screen_summary", summary, MAX_SUMMARY_CHARS)
