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


def _inside(inner: Any, outer: Any) -> bool:
    try:
        a, b = [int(v) for v in inner], [int(v) for v in outer]
    except (TypeError, ValueError, OverflowError):
        return False
    if len(a) != 4 or len(b) != 4:
        return False
    return a[0] >= b[0] and a[1] >= b[1] and a[2] <= b[2] and a[3] <= b[3]


def _inner_text(screen: Any, element: dict) -> str:
    """The first visible text inside *element*'s bounds (reading order).

    An unnamed clickable container (a Flutter or Compose card) is named by the label
    it holds, which a later run finds again; its id and bounds are not stable."""
    items = screen.get("elements") if isinstance(screen, dict) else None
    for other in items or []:
        if not isinstance(other, dict) or other is element:
            continue
        label = str(other.get("text") or other.get("label") or other.get("desc") or "")
        if label.strip() and _inside(other.get("bounds"), element.get("bounds")):
            return label.strip()
    return ""


def stamp_target(entry: dict, action: dict, screen: Any) -> None:
    """Set ``entry["target_snapshot"]`` when the action's target carries only an ``id``."""
    target = (action or {}).get("target")
    if not isinstance(target, dict) or not target.get("id"):
        return
    if any(target.get(k) for k in ("rid", "text", "label", "role")):
        return
    element = _element(screen, str(target["id"]))
    snap = _snapshot(element)
    if element and not any(snap.get(k) for k in ("rid", "text", "label")):
        inner = _inner_text(screen, element)
        snap = {**snap, "text": inner[:200]} if inner else snap
    if snap:
        entry["target_snapshot"] = snap
