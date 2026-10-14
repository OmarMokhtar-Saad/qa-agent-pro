"""Feature-scope text derived from the page when the source is thin."""

from __future__ import annotations

import logging
from typing import NamedTuple

logger = logging.getLogger("agents.test_scenario_agent")


def _page_title_for_scope(url_content: dict | None, ui_content: dict | None) -> str:
    """Page title from the UI extract, else from the fetched URL content."""
    title = ""
    if isinstance(ui_content, dict) and not ui_content.get("error"):
        title = ui_content.get("page_title") or ""
    if not title and isinstance(url_content, dict) and not url_content.get("error"):
        title = url_content.get("title") or ""
    return title


class PageElements(NamedTuple):
    field_descs: list[str]
    button_descs: list[str]


def _page_element_descs(ui_content: dict | None) -> PageElements:
    """(field descriptions, button labels) taken from the extracted UI."""
    field_descs: list[str] = []
    button_descs: list[str] = []
    if isinstance(ui_content, dict) and not ui_content.get("error"):
        ui = ui_content.get("ui_elements") or {}
        for f in (ui.get("form_fields") or [])[:15]:
            label = f.get("label") or f.get("name") or f.get("placeholder") or "field"
            field_descs.append(f"{label} ({f.get('type', 'text')})")
        for b in (ui.get("buttons") or [])[:10]:
            if b.get("text"):
                button_descs.append(b["text"])
    return PageElements(field_descs, button_descs)


def _derived_scope_text(title: str, url: str, elements: PageElements) -> str:
    header = f"Test the '{title}' page" if title else "Test the page"
    lines = [f"{header} at {url}."]
    if elements.field_descs:
        lines.append(
            "Input fields on this page: " + ", ".join(elements.field_descs) + "."
        )
    if elements.button_descs:
        lines.append("Buttons on this page: " + ", ".join(elements.button_descs) + ".")
    lines.append(
        "Scope every test case to the functionality actually present on THIS "
        "page. Do NOT invent separate pages, checkout, cart, or backend flows "
        "that are not reachable directly from the elements listed above."
    )
    return " ".join(lines)


def _is_bare_url(stripped: str) -> bool:
    return (
        stripped.lower().startswith(("http://", "https://"))
        and len(stripped.split()) == 1
    )


def _derive_from_page(
    stripped: str, url_content: dict | None, ui_content: dict | None
) -> str | None:
    title = _page_title_for_scope(url_content, ui_content)
    elements = _page_element_descs(ui_content)
    # No usable page context — leave the bare URL as-is (the upstream refuse
    # guard already handles the unreadable-ticket case).
    if not title and not (elements.field_descs or elements.button_descs):
        return None
    return _derived_scope_text(title, stripped, elements)


# Turn a bare-URL feature into a scoped description grounded in the page.
#
# A bare URL is a weak spec: the category fan-out invents unrelated pages and
# flows (checkout, cart, backend services on SauceDemo), and generate_acs
# receives a URL it tries to "fetch" instead of returning JSON. When the input
# is nothing but a URL, derive a short description from the fetched page title
# and the extracted UI (fields + buttons) so generation stays on THIS page.
#
# Returns feature_text unchanged for any real (non-bare-URL) feature text, or
# when there is no page context to ground a description in. Never raises.
def _scope_feature_text(
    feature_text: str,
    url_content: dict | None,
    ui_content: dict | None,
) -> str:
    """Scope a bare-URL feature to the fetched page; otherwise unchanged."""
    try:
        stripped = (feature_text or "").strip()
        if not _is_bare_url(stripped):
            return feature_text
        derived = _derive_from_page(stripped, url_content, ui_content)
        if derived is None:
            return feature_text
        logger.info(
            "Derived scoping feature description from bare URL: %s", derived[:200]
        )
        return derived
    except Exception:
        logger.exception("_scope_feature_text failed — using original feature_text")
        return feature_text
