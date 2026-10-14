"""Plain-text summary of extracted UI elements for tools/ui_extractor.py."""

from __future__ import annotations


def _items(ui_elements: dict, key: str) -> list:
    return ui_elements.get(key) or []


def _fields_line(fields: list) -> str:
    """``Form fields: label (type), ...`` or "" when there are none."""
    if not fields:
        return ""
    field_strs = [
        f"{f.get('label') or f.get('name') or f.get('type')} ({f.get('type')})"
        for f in fields
    ]
    return "Form fields: " + ", ".join(field_strs[:10])


def _buttons_line(buttons: list) -> str:
    """``Buttons: text, ...`` or "" when there are none."""
    if not buttons:
        return ""
    btn_strs = [b.get("text", "") for b in buttons if b.get("text")]
    return "Buttons: " + ", ".join(btn_strs[:8])


# Build a compact plain-text summary of extracted UI elements.
#
# Used as the `content` field so callers that only look at `content` get
# a useful string representation.
def _build_content_summary(page_title: str, ui_elements: dict) -> str:
    """Build a compact plain-text summary of extracted UI elements."""
    headings = _items(ui_elements, "headings")
    fields = _items(ui_elements, "form_fields")
    buttons = _items(ui_elements, "buttons")
    nav = _items(ui_elements, "navigation_links")
    interactive = _items(ui_elements, "interactive")
    lines = [
        f"Page: {page_title}" if page_title else "",
        "Headings: " + " | ".join(headings[:5]) if headings else "",
        _fields_line(fields),
        _buttons_line(buttons),
        "Navigation: " + ", ".join(nav[:8]) if nav else "",
        "Notes: " + " | ".join(interactive[:5])
        if interactive and not fields and not buttons
        else "",
    ]
    return "\n".join(line for line in lines if line)
