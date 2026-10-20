"""The short kind of an element (Button, Field, Text, ...), from its class and flags."""

from __future__ import annotations

# First match wins; a substring of the lower-cased class name. Generic Android
# widget vocabulary only: no app, label or layout is named here.
_BY_CLASS: tuple[tuple[str, str], ...] = (
    ("edittext", "Field"),
    ("autocomplete", "Field"),
    ("checkbox", "Check"),
    ("switch", "Check"),
    ("toggle", "Check"),
    ("radio", "Check"),
    ("button", "Button"),
    ("recycler", "List"),
    ("listview", "List"),
    ("gridview", "List"),
    ("scrollview", "List"),
    ("pager", "List"),
    ("webview", "Web"),
    ("image", "Image"),
    ("textview", "Text"),
)

#: Flags a kind already says, so the line does not print them twice.
IMPLIED_FLAGS: dict[str, tuple[str, ...]] = {
    "Button": ("clickable",),
    "Check": ("clickable",),
    "Field": ("editable", "clickable"),
    "List": ("scrollable",),
}


def kind_of(element: dict) -> str:
    """``Field``/``Check``/``Button``/``List``/``Web``/``Image``/``Text``, else ``View``."""
    if element.get("editable"):
        return "Field"
    cls = str(element.get("cls") or "").lower()
    for needle, kind in _BY_CLASS:
        if needle in cls:
            return kind
    return "List" if element.get("scrollable") else "View"
