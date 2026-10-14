"""Naming findings of the screen audit: who is the element, what is wrong with its name."""

from __future__ import annotations

from typing import NamedTuple, Optional

from tools.mobile import perception_text
from tools.mobile.saud_consts import (
    GENERIC_ID_WORDS,
    KIND_SECURE_LABEL_LEAK,
    KIND_UNLABELLED_FIELD,
    KIND_UNNAMED_CONTROL,
    MASK_CHARS,
    MAX_FINDING_CHARS,
)


def _unwrap(screen: object) -> dict:
    body = screen if isinstance(screen, dict) else {}
    inner = body.get("content")
    return inner if isinstance(inner, dict) else body


def _rid_name(rid: object) -> str:
    """A name derivable from a resource id, or ``""``.

    ``perception_text.words`` is the one tokeniser -- deriving the same answer from a
    second regex here is how two rules drift apart.
    """
    tail = str(rid or "").rsplit("/", 1)[-1]
    tokens = [
        word for word in perception_text.words(tail) if word not in GENERIC_ID_WORDS
    ]
    return " ".join(tokens)


def _is_interactive(element: dict) -> bool:
    return bool(element.get("clickable") or element.get("editable"))


class _Said(NamedTuple):
    ids: list
    name: str
    detail: str


class _Who(NamedTuple):
    eid: str
    label: str
    rid_name: str


def _finding(kind: str, said: _Said) -> dict:
    return {
        "kind": kind,
        "ids": [str(value) for value in said.ids][:12],
        "name": str(said.name or "")[:MAX_FINDING_CHARS],
        "detail": str(said.detail or "")[:MAX_FINDING_CHARS],
    }


def _masked(text: str) -> bool:
    stripped = str(text or "").strip()
    return bool(stripped) and not (set(stripped) - MASK_CHARS)


def _secure_leak(element: dict, who: _Who) -> Optional[dict]:
    if not element.get("secure"):
        return None
    text = str(element.get("text") or "").strip()
    if not text or _masked(text):
        return None
    detail = (
        "this password field's own dump carries "
        + str(len(text))
        + " characters of unmasked text, so the secret is "
        "readable by anything that reads the screen"
    )
    return _finding(KIND_SECURE_LABEL_LEAK, _Said([who.eid], who.label, detail))


def _unnamed_field(element: dict, who: _Who) -> Optional[dict]:
    if not element.get("editable") or who.label or who.rid_name:
        return None
    detail = (
        "an input with no text, no content-desc and no name "
        "in its resource id: a screen reader announces it as "
        "an edit box and nothing else"
    )
    return _finding(KIND_UNLABELLED_FIELD, _Said([who.eid], "", detail))


def _unnamed_control(element: dict, who: _Who) -> Optional[dict]:
    if element.get("editable") or not element.get("clickable"):
        return None
    if who.label or who.rid_name:
        return None
    detail = (
        "a tappable control with no text, no content-desc and "
        "no name in its resource id: there is nothing for a "
        "screen reader to say and no way to work around it"
    )
    return _finding(KIND_UNNAMED_CONTROL, _Said([who.eid], "", detail))


def _naming_findings(element: dict, who: _Who, findings: list) -> None:
    """Append the secret-leak and unnamed input/control findings for one element."""
    for check in (_secure_leak, _unnamed_field, _unnamed_control):
        found = check(element, who)
        if found:
            findings.append(found)
