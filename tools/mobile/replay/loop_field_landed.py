"""Replay engine: is the typed text visible in the SAME field on a settled screen?

A one-shot IME read-back taken mid re-layout can answer "" for a field that did
take the text. The settled screen's accessibility tree is the second witness:
the field that had focus before the type must now carry the typed text.
Never "any editable holding the text": a one-character text would match any
other field.
"""

from __future__ import annotations

from tools.mobile.text_fold import fold


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
