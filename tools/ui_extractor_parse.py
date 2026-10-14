"""HTML parsing for tools/ui_extractor.py: soup, headings, buttons, nav links."""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from tools.ui_extractor_fields import _extract_form_fields, _extract_interactive
from tools.ui_extractor_modal import _find_modal_triggers

logger = logging.getLogger("tools.ui_extractor")


def _extract_headings(soup: BeautifulSoup) -> list[str]:
    """Return text of all h1-h6 tags, stripping whitespace."""
    try:
        headings: list[str] = []
        for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
            text = tag.get_text(strip=True)
            if text:
                headings.append(text)
        return headings[:20]  # cap to avoid overwhelming the LLM
    except Exception:
        logger.exception("ui_extractor._extract_headings failed")
        return []


def _extract_buttons(soup: BeautifulSoup) -> list[dict]:
    """Return structured info for every button and input[type=submit]."""
    try:
        buttons: list[dict] = []
        for tag in soup.find_all("button"):
            text = tag.get_text(strip=True)
            btn_type = tag.get("type", "button")
            if text:
                buttons.append({"text": text, "type": btn_type})
        for tag in soup.find_all("input", type="submit"):
            text = tag.get("value") or "Submit"
            buttons.append({"text": text, "type": "submit"})
        return buttons[:20]  # cap
    except Exception:
        logger.exception("ui_extractor._extract_buttons failed")
        return []


def _extract_nav_links(soup: BeautifulSoup) -> list[str]:
    """Return visible link text from nav, header, and footer elements."""
    try:
        links: list[str] = []
        seen: set[str] = set()
        for container in soup.find_all(["nav", "header", "footer"]):
            for a in container.find_all("a"):
                text = a.get_text(strip=True)
                if text and text not in seen:
                    links.append(text)
                    seen.add(text)
        return links[:30]  # cap
    except Exception:
        logger.exception("ui_extractor._extract_nav_links failed")
        return []


def _make_soup(html: str) -> BeautifulSoup | None:
    """Parse `html` without script/style tags; None when parsing fails."""
    try:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style"]):
            tag.decompose()
        return soup
    except Exception:
        logger.exception("ui_extractor._parse_ui_elements: BeautifulSoup parse failed")
        return None


# Parse HTML and extract structured UI elements.
#
# Returns a dict with keys:
#   headings         (list[str])  -- text of all h1-h6 tags
#   form_fields      (list[dict]) -- each: {name, type, placeholder, required, label}
#   buttons          (list[dict]) -- each: {text, type}
#   navigation_links (list[str])  -- visible link text from nav/header/footer
#   interactive      (list[str])  -- select, textarea, checkbox element labels
#
# Never raises -- returns a partial result if parsing partially fails.
def _parse_ui_elements(html: str) -> dict:
    """Parse HTML and extract structured UI elements."""
    soup = _make_soup(html)
    if soup is None:
        return {}
    modal_ids, modal_triggers = _find_modal_triggers(soup)
    return {
        "headings": _extract_headings(soup),
        "form_fields": _extract_form_fields(soup, modal_ids, modal_triggers),
        "buttons": _extract_buttons(soup),
        "navigation_links": _extract_nav_links(soup),
        "interactive": _extract_interactive(soup),
    }
