"""A saved flow that does nothing: only waits and optional taps (no required step).

Such a flow was saved from a screen the run was already on (the post-login home): every
step starts on ONE screen and none changes the app, so replaying it only dismisses pop-ups
and waits for text of the old run. It is never offered, never auto-replayed and never saved
(``kstg_flow_gate``). Pure; never raises.
"""

from __future__ import annotations

_OPTIONAL = ("tap_text_if_present",)


def optional_op(op: object) -> bool:
    """Whether *op* never changes the app by itself (a wait or an optional tap)."""
    name = str(op or "")
    return name.startswith("wait_") or name in _OPTIONAL


def still(before: object, after: object) -> bool:
    """True when a step leaves the screen as it was (or its result was never recorded)."""
    return not after or after == before


def inert_ops(ops: list, keys: list | None = None) -> bool:
    """True for a non-empty op list with no required step that moves the screen.

    *keys* are the ``(screen key before, after)`` pairs of the steps: an optional step
    that changes the screen (a navigating "Skip" chain) is not inert."""
    moved = any(not still(b, a) for b, a in keys or [])
    return bool(ops) and not moved and all(optional_op(op) for op in ops)


def _pairs(template: dict) -> list:
    meta = template.get("meta") or {}
    before, after = list(meta.get("before") or []), list(meta.get("after") or [])
    return [(before[i] if i < len(before) else "", a) for i, a in enumerate(after)]


def inert(template: dict) -> bool:
    """True when the flow *template* has steps, none required and none moving the screen."""
    steps = [s for s in template.get("steps") or [] if isinstance(s, dict)]
    return inert_ops([s.get("op") for s in steps], _pairs(template))
