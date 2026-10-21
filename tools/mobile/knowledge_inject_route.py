"""Inject source route map: the screens and taps the app is known to have.

Saved edges (``from screen --tap--> to screen``) are stored by opaque screen key, so
a screen is told by the controls the run tapped on it. One short block, most-seen
edges first, so the model can plan a route instead of exploring it again.
"""

from __future__ import annotations

from tools.mobile import knowledge_inject

MAX_EDGES = 12
_OPS = ("tap", "tap_text", "type", "fill")
_NAME_ITEMS = 3


def _usable(row: dict) -> bool:
    return bool(row.get("target")) and row.get("action") in _OPS


def _named(snap: dict) -> dict:
    """``{screen key: "'a', 'b'"}``: the controls tapped on each screen."""
    out: dict = {}
    for key, rows in (snap.get("edges") or {}).items():
        names: list = []
        for row in rows:
            tag = str(row.get("target") or "")
            if _usable(row) and tag not in names:
                names.append(tag)
        if names:
            out[key] = ", ".join("'%s'" % n for n in names[:_NAME_ITEMS])
    return out


def _rows(snap: dict, names: dict) -> list:
    rows = [
        (key, row)
        for key, group in (snap.get("edges") or {}).items()
        for row in group
        if key in names and _usable(row)
    ]
    rows.sort(key=lambda kr: (-int(kr[1].get("count") or 0), int(kr[1].get("id") or 0)))
    return rows[:MAX_EDGES]


def _line(key: str, row: dict, names: dict) -> str:
    end = names.get(row.get("to_key"), "")
    where = " reaches the screen offering %s" % end if end else " moves on"
    head = "on the screen offering %s: %s '%s'" % (
        names[key],
        row["action"],
        row["target"],
    )
    return head + where


def route_map(snap: dict, key) -> list:
    """ONE item: the known route map; stable block only."""
    if key is not None:
        return []
    names = _named(snap)
    lines = [_line(k, r, names) for k, r in _rows(snap, names)]
    if not lines:
        return []
    text = "known route map (screens seen in earlier runs): " + "; ".join(lines) + "."
    return [{"id": "route:map", "table": "edges", "prio": 2, "text": text}]


knowledge_inject.register_source("route", route_map)
