"""Replay engine: the ``tap_text`` and ``fill`` element lookups. Cut verbatim from tools/mobile/replay/steps.py (C5 w17)."""

from __future__ import annotations

from typing import Any

from tools.mobile import actions as actions_mod
from tools.mobile import fill_label
from tools.mobile.replay.context import STATUS_NEEDS_MODEL, _ReplayRun, _result
from tools.mobile.replay.elements import _screen_elements, resolve_tap_text
from tools.mobile.replay.steps_stop import StepAt, _close, _reply, _stop


def _ambiguous_detail(picked: dict, wanted: str) -> str:
    """The detail for a ``tap_text`` that named several controls or none."""
    if not picked.get("ambiguous"):
        return "Nothing on this screen carries the text `" + wanted + "`."
    return (
        "`"
        + wanted
        + "` is on "
        + str(picked.get("candidates"))
        + " different controls on this screen, so nothing was "
        "tapped. Use `tap` with a narrower target to say which "
        "one you mean."
    )


def _tap_text_element(
    run: _ReplayRun, action: Any, at: StepAt
) -> tuple[Any, dict | None]:
    """A ``tap_text`` step's element, or ``None`` and the reply that stops on a
    text that names no control or several."""
    picked = resolve_tap_text(getattr(action, "text", ""), at.screen)
    element = picked.get("element")
    if element is not None:
        return element, None
    wanted = str(getattr(action, "text", ""))[:80]
    outcome = (
        actions_mod.TAP_TEXT_AMBIGUOUS if picked.get("ambiguous") else "missing_element"
    )
    at.entry.update(outcome=outcome, detail=_ambiguous_detail(picked, wanted))
    _close(run, at)
    return element, _reply(_result(_stop(run, at, STATUS_NEEDS_MODEL)))


def _fill_miss(run: _ReplayRun, at: StepAt, detail: str) -> dict:
    """The reply that stops on a ``fill`` label naming no field or several."""
    at.entry.update(outcome="missing_element", detail=detail)
    _close(run, at)
    return _reply(_result(_stop(run, at, STATUS_NEEDS_MODEL)))


def _fill_element(run: _ReplayRun, action: Any, at: StepAt) -> tuple[Any, dict | None]:
    """A ``fill`` step's field, or ``None`` and the reply that stops on a label
    that names no field or several."""
    label = getattr(action, "label", "")
    picked = fill_label.resolve_fill(label, _screen_elements(at.screen))
    element = picked.get("element")
    if element is not None:
        return element, None
    detail = fill_label.refusal_detail(label, picked)
    return element, _fill_miss(run, at, detail)
