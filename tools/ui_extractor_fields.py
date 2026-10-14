"""Form-field and interactive-element extraction for tools/ui_extractor.py."""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from tools.ui_extractor_modal import _field_modal_trigger

logger = logging.getLogger("tools.ui_extractor")

_SKIPPED_TYPES = ("hidden", "submit", "button", "image", "reset")


def _field_input_type(tag) -> str:
    """The ``type`` of an input, or the tag name for select/textarea."""
    return tag.get("type", "text") if tag.name == "input" else tag.name


def _field_label(soup: BeautifulSoup, tag) -> str:
    """Label text via the `for` attribute, else a wrapping <label>, else ""."""
    tag_id = tag.get("id")
    if tag_id:
        label_el = soup.find("label", attrs={"for": tag_id})
        if label_el:
            text = label_el.get_text(strip=True)
            if text:
                return text
    parent_label = tag.find_parent("label")
    if parent_label:
        return parent_label.get_text(strip=True)
    return ""


def _field_entry(soup: BeautifulSoup, tag, modal: tuple) -> dict | None:
    """Field dict for *tag*, or None for hidden/submit/button inputs.

    ``modal`` is ``(modal_ids, modal_triggers)``.
    """
    input_type = _field_input_type(tag)
    # Skip hidden and submit/button inputs -- they're captured elsewhere
    if input_type in _SKIPPED_TYPES:
        return None
    return {
        "name": tag.get("name") or tag.get("id") or "",
        "type": input_type,
        "placeholder": tag.get("placeholder") or "",
        "required": tag.has_attr("required"),
        "label": _field_label(soup, tag),
        # Non-None only when the field is inside a pop-up/modal that
        # must be opened first (names the trigger control).
        "modal_trigger": _field_modal_trigger(tag, *modal),
    }


def _extract_form_fields(
    soup: BeautifulSoup,
    modal_ids: set[str] | None = None,
    modal_triggers: dict[str, str] | None = None,
) -> list[dict]:
    """Return structured info for every input, select, and textarea element.

    When a field sits inside a modal/pop-up, its dict carries a ``modal_trigger``
    key naming the control that must be clicked to open the modal first.
    """
    modal = (modal_ids or set(), modal_triggers or {})
    try:
        tags = soup.find_all(["input", "select", "textarea"])
        entries = [_field_entry(soup, tag, modal) for tag in tags]
        return [e for e in entries if e is not None][:30]  # cap
    except Exception:
        logger.exception("ui_extractor._extract_form_fields failed")
        return []


def _select_items(soup: BeautifulSoup) -> list[str]:
    """``name: option, option`` per select dropdown that has options."""
    items: list[str] = []
    for sel in soup.find_all("select"):
        name = sel.get("name") or sel.get("id") or "dropdown"
        options = [
            opt.get_text(strip=True)
            for opt in sel.find_all("option")
            if opt.get_text(strip=True)
        ]
        if options:
            items.append(f"{name}: {', '.join(options[:8])}")
    return items


def _textarea_items(soup: BeautifulSoup) -> list[str]:
    """``textarea: name`` per textarea."""
    items: list[str] = []
    for ta in soup.find_all("textarea"):
        name = ta.get("name") or ta.get("placeholder") or ta.get("id") or "textarea"
        items.append(f"textarea: {name}")
    return items


def _checkbox_items(soup: BeautifulSoup) -> list[str]:
    """``checkbox: name`` per checkbox input."""
    items: list[str] = []
    for cb in soup.find_all("input", type="checkbox"):
        name = cb.get("name") or cb.get("id") or "checkbox"
        items.append(f"checkbox: {name}")
    return items


def _extract_interactive(soup: BeautifulSoup) -> list[str]:
    """Return labels/aria-labels from select, textarea, and checkbox elements."""
    try:
        # select dropdowns capture their options, then textareas, then checkboxes
        items = _select_items(soup) + _textarea_items(soup) + _checkbox_items(soup)
        return items[:20]  # cap
    except Exception:
        logger.exception("ui_extractor._extract_interactive failed")
        return []
