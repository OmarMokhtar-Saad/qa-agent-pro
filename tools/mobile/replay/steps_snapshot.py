"""Stamp a trace entry with the stable selector of an id-only tap target.

An element ``id`` is positional and changes on every dump, so a saved flow cannot use
it. At record time the screen is in hand: find the element the id names and keep its
rid / text / label / role beside the entry, so the flow can aim by those instead.
"""

from __future__ import annotations

from typing import Any


def _element(screen: Any, want: str) -> dict:
    items = screen.get("elements") if isinstance(screen, dict) else None
    for element in items or []:
        if isinstance(element, dict) and str(element.get("id") or "") == want:
            return element
    return {}


def _snapshot(element: dict) -> dict:
    raw = {
        "rid": element.get("rid"),
        "text": element.get("text"),
        "label": element.get("desc") or element.get("label"),
        "role": element.get("role"),
    }
    return {k: str(v)[:200] for k, v in raw.items() if v}


def stamp_target(entry: dict, action: dict, screen: Any) -> None:
    """Set ``entry["target_snapshot"]`` when the action's target carries only an ``id``."""
    target = (action or {}).get("target")
    if not isinstance(target, dict) or not target.get("id"):
        return
    if any(target.get(k) for k in ("rid", "text", "label", "role")):
        return
    snap = _snapshot(_element(screen, str(target["id"])))
    if snap:
        entry["target_snapshot"] = snap
