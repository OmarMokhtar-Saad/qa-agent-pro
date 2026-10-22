"""Replay engine: is the typed text visible in the SAME field on a settled screen?

A one-shot IME read-back taken mid re-layout can answer "" for a field that did
take the text. The settled screen's accessibility tree is the second witness:
the field that had focus before the type must now carry the typed text.
Never "any editable holding the text": a one-character text would match any
other field.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from tools.mobile.text_fold import fold


class Typed(NamedTuple):
    """The text a step typed, and whether a ``clear`` step directly preceded it."""

    text: Any
    after_clear: bool = False


def _editables(screen: object) -> list[dict]:
    """The editable elements of *screen*."""
    rows = screen.get("elements") if isinstance(screen, dict) else None
    return [r for r in rows or [] if isinstance(r, dict) and r.get("editable")]


def _focused(screen: object) -> dict | None:
    """The focused editable of *screen*, if any."""
    return next((r for r in _editables(screen) if r.get("focused")), None)


def _is_same_field(candidate: dict, reference: dict) -> bool:
    """Same resource id; a field without one is matched by focus instead."""
    rid = str(reference.get("rid") or "")
    if rid:
        return str(candidate.get("rid") or "") == rid
    return bool(candidate.get("focused"))


def _holds(element: dict, typed: str) -> bool:
    """Whether the element's text or desc contains the (folded) typed text."""
    return any(typed in fold(element.get(key)) for key in ("text", "desc"))


def landed_on_screen(screen: object, typed: object, before: object) -> bool:
    """True when the field focused in *before* (else focused in *screen*) holds *typed*."""
    want = fold(typed)
    reference = _focused(before) or _focused(screen)
    if not want or reference is None:
        return False
    return any(
        _is_same_field(row, reference) and _holds(row, want)
        for row in _editables(screen)
    )


def _cleared_reference(before: object) -> dict | None:
    """The field a clear emptied: the focused one, else the only one with a resource id."""
    ruled = [r for r in _editables(before) if r.get("rid")]
    return _focused(before) or (ruled[0] if len(ruled) == 1 else None)


def _alnum(value: object) -> str:
    """The folded letters and digits of *value* (punctuation and spaces dropped)."""
    return "".join(c for c in fold(value) if c.isalnum())


def _in_order(typed: str, shown: str) -> bool:
    """Whether every char of *typed* appears in *shown*, in order (a formatter may add more)."""
    rest = iter(shown)
    return bool(typed) and all(c in rest for c in typed)


def _same_field_rows(screen: object, reference: dict) -> list[dict]:
    """The same-field rows of *screen*; the focused ones alone when any has focus."""
    rows = [r for r in _editables(screen) if _is_same_field(r, reference)]
    return [r for r in rows if r.get("focused")] or rows


def _changed_in_field(screen: object, typed: object, before: object) -> bool:
    """True when the field cleared before the type now shows the typed text, reshaped.

    *before* may be the PRE-clear screen (the settle skips the re-dump), so a hint,
    a default prefix or another row's text must not count: the typed letters and
    digits must appear in order in the field (a phone or IBAN formatter passes).
    """
    reference = _cleared_reference(before)
    want = _alnum(typed)
    if reference is None or not want:
        return False
    old = fold(reference.get("text"))
    return any(
        fold(row.get("text")) not in ("", old)
        and _in_order(want, _alnum(row.get("text")))
        for row in _same_field_rows(screen, reference)
    )


def shows_typed(screen: object, typed: Typed, before: object) -> bool:
    """Whether *screen* proves the text landed in the same field (E-01)."""
    if landed_on_screen(screen, typed.text, before):
        return True
    return typed.after_clear and _changed_in_field(screen, typed.text, before)
