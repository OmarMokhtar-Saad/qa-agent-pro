"""Replay engine: a ``fill`` or ``clear`` whose work is already done (fix/1460 W4).

Narrow on purpose: only a plainly readable field is judged. A masked field
(password, ``secure``) reads empty or bullets whatever it holds, a ``secret``
value is never compared, and a tap is never guessed at.
"""

from __future__ import annotations

from typing import Any

from tools.mobile.replay.context import _ReplayRun, _Step
from tools.mobile.replay.device_ops_text import _focus_field
from tools.mobile.replay.steps_stop import StepAt, _close

FILLED_DETAIL = "already filled, skipped"
EMPTY_DETAIL = "already empty, skipped"


def _masked(element: dict) -> bool:
    """A field whose dump text cannot be trusted: a password input."""
    return bool(element.get("secure") or element.get("role") == "password")


def _field_text(element: dict) -> str:
    """The text a field really holds: an old dump shows the HINT as the text of an empty one."""
    text = str(element.get("text") or "").strip()
    hint = str(element.get("hint") or "").strip()
    return "" if hint and text == hint else text


def _names_the_field(element: dict, text: str) -> bool:
    """Whether *text* is just the field's own name: before Android 8 a dump shows the
    placeholder as the text and has no ``hint`` to tell it apart."""
    return text in {
        str(element.get(k) or "").strip() for k in ("hint", "desc", "label")
    }


def _fill_done(step: _Step, element: dict) -> bool:
    """A non-secret ``fill`` whose field already reads exactly its value."""
    value = str(getattr(step.action, "text", "") or "").strip()
    secret = bool(getattr(step.action, "secret", False))
    shown = _field_text(element)
    return (
        bool(value)
        and not secret
        and shown == value
        and not _names_the_field(element, shown)
    )


def _clear_done(element: dict) -> bool:
    """A ``clear`` of a field that reads empty AND is known to expose its text.

    The dump's ``hint`` key is the proof (an Android 8+ dump separates the hint from
    the text); a field that exposes no text at all (some Flutter / WebView inputs)
    reads empty whatever it holds.
    """
    return (
        bool(element.get("editable")) and "hint" in element and not _field_text(element)
    )


def already_done(step: _Step) -> str:
    """The skip detail when the step's field already holds what it would set, else ``""``."""
    element = step.element
    if not isinstance(element, dict) or _masked(element):
        return ""
    if step.op == "fill" and _fill_done(step, element):
        return FILLED_DETAIL
    if step.op == "clear" and _clear_done(element):
        return EMPTY_DETAIL
    return ""


async def skip_if_done(run: _ReplayRun, step: _Step, screen: Any) -> tuple | None:
    """``(screen, None)`` after recording the step ``ok`` (skipped), else ``None``.

    The real op taps the field before it types; a skipped one still does, so a
    later enter / IME search acts on this field. A failed tap skips nothing: the
    real op runs and reports it.
    """
    detail = already_done(step)
    if not detail or await _focus_field(run.ctx.serial, step.element) is not None:
        return None
    step.entry.update(outcome="ok", detail=detail)
    _close(run, StepAt(step.entry["index"], step.entry, screen))
    return screen, None
