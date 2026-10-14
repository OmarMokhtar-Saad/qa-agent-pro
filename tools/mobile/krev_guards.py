"""The guards half of the "Learned this run" provider: notes that fired."""

from __future__ import annotations

from tools.mobile.krev_entry import Entry, _item


def _fired(node: object, depth: int = 0) -> list:
    found: list = []
    if depth > 6:
        return found
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "knowledge" and isinstance(value, list):
                found += [v for v in value if isinstance(v, dict) and "note_id" in v]
            else:
                found += _fired(value, depth + 1)
    elif isinstance(node, list):
        for value in node[:500]:
            found += _fired(value, depth + 1)
    return found


def _guard_item(fired: dict) -> dict:
    title = "note #%s %s" % (fired.get("note_id"), fired.get("kind") or "")
    detail = "%s, %s ms" % (fired.get("outcome") or "", int(fired.get("ms") or 0))
    return _item("notes", str(fired.get("note_id") or ""), Entry(title, detail))
