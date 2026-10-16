"""Start a saved flow by its own target when the screen key drifted.

A screen may carry a second key (a dialog over it, a one-time banner) so no saved key
equals the live one. A step may then still start when the live screen shows exactly
one element matching the step's target, but only for the flow's first step and never
on a screen the flow already knows.
"""

from __future__ import annotations

from typing import Any

FIELDS = ("text", "rid", "label", "role")


class Keys(tuple):
    """The keys a live screen may be stored under, plus that screen itself."""

    screen: Any = None

    def __new__(cls, keys: Any = (), screen: Any = None) -> "Keys":
        self = super().__new__(cls, keys)
        self.screen = screen
        return self


def _wanted(step: dict) -> dict:
    """The element properties *step* acts on; empty for an optional or unaimed step."""
    if "if_present" in str(step.get("op") or ""):
        return {}
    target = step.get("target")
    wanted = {k: str(target[k]) for k in FIELDS if target.get(k)} if target else {}
    if not wanted and step.get("op") == "fill" and step.get("label"):
        wanted = {"label": str(step["label"])}
    return wanted


def _is(element: dict, wanted: dict) -> bool:
    return all(str(element.get(k) or "") == v for k, v in wanted.items())


def _known(template: dict, keys: tuple) -> bool:
    meta = template.get("meta") or {}
    own = {k for w in ("before", "after") for k in meta.get(w) or []}
    return bool(own & set(keys))


def fits(template: dict, keys: Any, at: int) -> bool:
    """Whether step *at* may start on the live screen carried by *keys*."""
    screen = getattr(keys, "screen", None)
    elements = screen.get("elements") if isinstance(screen, dict) else None
    steps = template.get("steps") or []
    if not elements or at or not steps:
        return False
    wanted = _wanted(steps[at])
    if not wanted or _known(template, keys):
        return False
    return sum(_is(e, wanted) for e in elements if isinstance(e, dict)) == 1
