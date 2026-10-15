"""The fallback for a ``fill`` whose label names no field.

A label that misses is not always a wrong label: the screen often has exactly
one empty field that can take typed text, and the label was only a guess at its
name. This is a pure function over the pruned element list. One candidate is
filled (and the reply says so); several are named so the model can retry with a
label that matches, without a fresh dump.

A field is input-capable when it is EDITABLE and empty, or an empty custom
``View`` whose own description calls it an input (an OTP / PIN box drawn by
the app, which takes IME input but reports ``editable=false``).
"""

from __future__ import annotations

import re

from tools.mobile.fill_label import FILL_MAX_CANDIDATES_LISTED
from tools.untrusted import single_line, wrap_untrusted

_WORD = re.compile(r"\b(input|otp|pin|passcode|digits?)\b", re.IGNORECASE)


def _name(element: dict) -> str:
    """The visible name of a field: label, description, hint or id."""
    for key in ("label", "desc", "hint", "rid"):
        if element.get(key):
            return str(element[key])
    return str(element.get("cls") or "field")


def _custom_input(element: dict) -> bool:
    """An empty plain ``View`` that describes itself as an input."""
    if str(element.get("cls") or "") != "View":
        return False
    return bool(_WORD.search(str(element.get("desc") or element.get("label") or "")))


def _input_capable(element: dict) -> bool:
    if element.get("text"):
        return False
    return bool(element.get("editable")) or _custom_input(element)


def candidates(elements: object) -> list[dict]:
    """The empty input-capable fields on the screen, in order."""
    rows = elements if isinstance(elements, (list, tuple)) else []
    return [e for e in rows if isinstance(e, dict) and _input_capable(e)]


_TOKEN = re.compile(r"[^\W_]{2,}")
_CODE = re.compile(r"\b(otp|pin|passcode|code|digits?|verification)\b", re.I)


#: Words every field label shares; an overlap on these proves nothing.
_FILLER = frozenset(
    {"enter", "your", "the", "type", "input", "field", "here", "please"}
)


def _words(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower())) - _FILLER


def fits(label: str, element: dict) -> bool:
    """Whether the field may stand in for the label: it has no name of its own
    (an OTP box), or its name shares a word with the label, or both read as a
    code. A field named for something else ("Email" for "Enter OTP") never does.
    """
    own = " ".join(str(element.get(k) or "") for k in ("label", "desc", "hint"))
    if not own.strip():
        return True
    if _words(own) & _words(label):
        return True
    return bool(_CODE.search(own)) and bool(_CODE.search(label))


def used_note(label: str, element: dict) -> str:
    """The sentence that says which field was filled in place of the label."""
    return (
        "No field matched `"
        + single_line(label, 60)
        + "`; filled the only empty input field, "
        + repr(single_line(_name(element), 60))
        + "."
    )


def listing(found: list[dict]) -> str:
    """The empty fields' names, fenced as untrusted, for a retry."""
    names = [single_line(_name(e), 60) for e in found[:FILL_MAX_CANDIDATES_LISTED]]
    return " Empty input fields: " + wrap_untrusted("field labels", "; ".join(names))
