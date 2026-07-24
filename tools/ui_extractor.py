"""UI Extractor -- parse live web pages into structured UI element data.

Uses a tiered fetch strategy so it works on both server-rendered pages and
JavaScript single-page apps (React/Vue/Angular, e.g. SauceDemo):

  Tier 1 -- tools/jira_fetcher.py (httpx). Fast, server-rendered pages. When it
            flags spa_shell=True (a JS-only shell) or its HTML yields zero UI
            elements, escalate to Tier 2.
  Tier 2 -- tools/browser_renderer.py (Playwright headless Chromium). Renders
            the page for real; the resulting HTML is parsed with the same
            BeautifulSoup extractors used for Tier 1. Degrades cleanly if
            Playwright/Chromium isn't installed.
  Tier 3 -- llm.ask_vision() (api backend only). Only used when Tier 2's
            rendered HTML still yields zero elements (e.g. a canvas-only UI).
            Cleanly skipped (no crash, no user-facing error) when
            QA_LLM_BACKEND=cli.

Contract:
- Never raises -- always returns a dict.
- On success: {"ui_elements": {...}, "page_title": str, "content": str,
  "extraction_method": "static_html"|"js_rendered"|"vision"|"unavailable"|"none",
  "error": None}
- On failure: {"error": str, "content": None}
"""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from llm import ask_vision
from tools.browser_renderer import render_page
from tools.jira_fetcher import fetch_url_content

logger = logging.getLogger(__name__)

_VISION_SYSTEM_PROMPT = (
    "You are inspecting a screenshot of a web page for a QA test-case generator. "
    "List every visible interactive element you can identify: buttons (with their "
    "visible label), input fields (with placeholder or nearby label text), links, "
    "and headings. Respond in concise plain text, one element per line. If you "
    "cannot identify any interactive elements, say so plainly."
)


async def extract_ui_elements(url: str, prefetched: dict | None = None) -> dict:
    """Fetch a live URL and extract structured UI elements from its HTML.

    Returns a dict with keys:
      ui_elements (dict) -- structured element data grouped by category
      page_title  (str)  -- <title> text
      content     (str)  -- plain-text summary used as fallback context
      extraction_method (str) -- which tier produced the result
      error       (None) -- always None on success

    When *prefetched* is a non-None, error-free result from a prior
    ``fetch_url_content(url)`` call, it is reused instead of fetching the URL a
    second time (B-008/D-3). A None or error-bearing *prefetched* falls back to
    the normal internal fetch, so behaviour is identical when it is not supplied.

    On any failure returns {"error": str, "content": None}.
    Never raises.
    """
    try:
        if isinstance(prefetched, dict) and not prefetched.get("error"):
            fetch_result = prefetched
        else:
            fetch_result = await fetch_url_content(url)
        if fetch_result.get("error"):
            logger.warning(
                "ui_extractor: fetch_url_content failed for %s: %s",
                url,
                fetch_result["error"],
            )
            return {"error": fetch_result["error"], "content": None}

        raw_html = (
            fetch_result.get("raw_html")
            or fetch_result.get("raw_text")
            or fetch_result.get("content")
            or ""
        )
        page_title = fetch_result.get("title") or ""
        spa_shell = bool(fetch_result.get("spa_shell"))

        if not raw_html and not spa_shell:
            logger.warning(
                "ui_extractor: no HTML content returned for %s -- returning text fallback",
                url,
            )
            return {
                "ui_elements": {},
                "page_title": page_title,
                "content": fetch_result.get("description") or "",
                "extraction_method": "none",
                "render_error": None,
                "error": None,
            }

        ui_elements = _parse_ui_elements(raw_html) if raw_html else {}
        extraction_method = "static_html"
        # Carries the Tier 2 render failure reason (Playwright missing, nav
        # timeout, ...) up to the caller so a "no elements" outcome can be
        # explained specifically instead of with a single generic message.
        render_error: str | None = None

        if spa_shell or _looks_empty(ui_elements):
            logger.info(
                "ui_extractor: escalating to Tier 2 browser render for %s (spa_shell=%s)",
                url,
                spa_shell,
            )
            rendered = await render_page(url)
            if rendered.get("error"):
                render_error = rendered["error"]
                logger.warning(
                    "ui_extractor: Tier 2 browser render unavailable/failed for %s: %s",
                    url,
                    rendered["error"],
                )
            else:
                rendered_html = rendered.get("html") or ""
                if rendered_html:
                    rendered_elements = _parse_ui_elements(rendered_html)
                    if not _looks_empty(rendered_elements):
                        ui_elements = rendered_elements
                        extraction_method = "js_rendered"
                        if rendered.get("title"):
                            page_title = rendered["title"]

            if _looks_empty(ui_elements):
                screenshot = (
                    rendered.get("screenshot") if not rendered.get("error") else None
                )
                if screenshot:
                    vision_text = await _describe_via_vision(screenshot, url)
                    if vision_text and not vision_text.startswith("Error:"):
                        ui_elements = {
                            "headings": [],
                            "form_fields": [],
                            "buttons": [],
                            "navigation_links": [],
                            "interactive": [vision_text],
                        }
                        extraction_method = "vision"
                    else:
                        extraction_method = "unavailable"
                else:
                    extraction_method = "unavailable"

        content_summary = _build_content_summary(page_title, ui_elements)
        if not content_summary:
            content_summary = fetch_result.get("description") or ""

        logger.info(
            "ui_extractor: extracted %d headings, %d fields, %d buttons, %d links "
            "from %s (method=%s)",
            len(ui_elements.get("headings") or []),
            len(ui_elements.get("form_fields") or []),
            len(ui_elements.get("buttons") or []),
            len(ui_elements.get("navigation_links") or []),
            url,
            extraction_method,
        )
        return {
            "ui_elements": ui_elements,
            "page_title": page_title,
            "content": content_summary,
            "extraction_method": extraction_method,
            "render_error": render_error,
            "error": None,
        }
    except Exception as exc:
        logger.exception("ui_extractor: unexpected error for %s", url)
        return {"error": str(exc), "content": None}


def _looks_empty(ui_elements: dict) -> bool:
    """True when ui_elements has no usable content in any category."""
    if not ui_elements:
        return True
    return not any(
        ui_elements.get(k)
        for k in (
            "headings",
            "form_fields",
            "buttons",
            "navigation_links",
            "interactive",
        )
    )


async def _describe_via_vision(screenshot_bytes: bytes, url: str) -> str:
    """Tier 3 -- ask Claude to describe UI elements visible in a screenshot.

    Returns llm.ask_vision()'s raw string result, including its "Error: ..."
    sentinel on failure/unavailability. Never raises.
    """
    user = (
        f"Page URL: {url}\nDescribe the UI elements visible in the attached screenshot."
    )
    try:
        return await ask_vision(_VISION_SYSTEM_PROMPT, user, screenshot_bytes)
    except Exception:
        logger.exception("ui_extractor: vision fallback call failed for %s", url)
        return "Error: vision call failed"


def _parse_ui_elements(html: str) -> dict:
    """Parse HTML and extract structured UI elements.

    Returns a dict with keys:
      headings         (list[str])  -- text of all h1-h6 tags
      form_fields      (list[dict]) -- each: {name, type, placeholder, required, label}
      buttons          (list[dict]) -- each: {text, type}
      navigation_links (list[str])  -- visible link text from nav/header/footer
      interactive      (list[str])  -- select, textarea, checkbox element labels

    Never raises -- returns a partial result if parsing partially fails.
    """
    try:
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style"]):
            tag.decompose()
    except Exception:
        logger.exception("ui_extractor._parse_ui_elements: BeautifulSoup parse failed")
        return {}

    modal_ids, modal_triggers = _find_modal_triggers(soup)

    return {
        "headings": _extract_headings(soup),
        "form_fields": _extract_form_fields(soup, modal_ids, modal_triggers),
        "buttons": _extract_buttons(soup),
        "navigation_links": _extract_nav_links(soup),
        "interactive": _extract_interactive(soup),
    }


def _find_modal_triggers(soup: BeautifulSoup) -> tuple[set[str], dict[str, str]]:
    """Map each modal/dialog element id to the visible label of the control that
    opens it.

    Many sites (e.g. instakidzapp.com) put their signup/demo form inside a
    Bootstrap modal that is hidden until the tester clicks a "Book Demo" /
    "Get Started Free" trigger (``data-bs-target="#id"`` / ``data-target="#id"``
    / ``href="#id"``). Fields inside such a modal ARE in the DOM (so they get
    extracted) but are NOT reachable until the modal is opened — the generated
    steps must click the trigger first. Returns (modal_ids, {modal_id: trigger}).
    Never raises.
    """
    try:
        modal_ids: set[str] = set()
        for m in soup.select(".modal"):
            if m.get("id"):
                modal_ids.add(m.get("id"))
        for m in soup.find_all(attrs={"role": "dialog"}):
            if m.get("id"):
                modal_ids.add(m.get("id"))

        triggers: dict[str, str] = {}
        for attr in ("data-bs-target", "data-target", "href"):
            for el in soup.find_all(attrs={attr: True}):
                val = (el.get(attr) or "").strip()
                if val.startswith("#") and val[1:] in modal_ids:
                    text = el.get_text(strip=True)
                    if text and val[1:] not in triggers:
                        triggers[val[1:]] = text
        return modal_ids, triggers
    except Exception:
        logger.exception("ui_extractor._find_modal_triggers failed")
        return set(), {}


def _field_modal_trigger(
    tag, modal_ids: set[str], triggers: dict[str, str]
) -> str | None:
    """If *tag* sits inside a known modal, return the label of the control that
    opens that modal (falling back to the modal id), else None. Never raises."""
    try:
        anc = tag
        for _ in range(15):
            anc = getattr(anc, "parent", None)
            if anc is None:
                break
            aid = anc.get("id") if hasattr(anc, "get") else None
            if aid and aid in modal_ids:
                return triggers.get(aid) or aid
        return None
    except Exception:
        return None


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


def _extract_form_fields(
    soup: BeautifulSoup,
    modal_ids: set[str] | None = None,
    modal_triggers: dict[str, str] | None = None,
) -> list[dict]:
    """Return structured info for every input, select, and textarea element.

    When a field sits inside a modal/pop-up, its dict carries a ``modal_trigger``
    key naming the control that must be clicked to open the modal first.
    """
    modal_ids = modal_ids or set()
    modal_triggers = modal_triggers or {}
    try:
        fields: list[dict] = []
        for tag in soup.find_all(["input", "select", "textarea"]):
            tag_type = tag.name
            input_type = tag.get("type", "text") if tag_type == "input" else tag_type
            # Skip hidden and submit/button inputs -- they're captured elsewhere
            if input_type in ("hidden", "submit", "button", "image", "reset"):
                continue
            name = tag.get("name") or tag.get("id") or ""
            placeholder = tag.get("placeholder") or ""
            required = tag.has_attr("required")
            # Try to find an associated <label> via `for` attribute or wrapping element
            label_text = ""
            tag_id = tag.get("id")
            if tag_id:
                label_el = soup.find("label", attrs={"for": tag_id})
                if label_el:
                    label_text = label_el.get_text(strip=True)
            if not label_text:
                parent_label = tag.find_parent("label")
                if parent_label:
                    label_text = parent_label.get_text(strip=True)
            fields.append(
                {
                    "name": name,
                    "type": input_type,
                    "placeholder": placeholder,
                    "required": required,
                    "label": label_text,
                    # Non-None only when the field is inside a pop-up/modal that
                    # must be opened first (names the trigger control).
                    "modal_trigger": _field_modal_trigger(
                        tag, modal_ids, modal_triggers
                    ),
                }
            )
        return fields[:30]  # cap
    except Exception:
        logger.exception("ui_extractor._extract_form_fields failed")
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


def _extract_interactive(soup: BeautifulSoup) -> list[str]:
    """Return labels/aria-labels from select, textarea, and checkbox elements."""
    try:
        items: list[str] = []
        # select dropdowns -- capture their options
        for sel in soup.find_all("select"):
            name = sel.get("name") or sel.get("id") or "dropdown"
            options = [
                opt.get_text(strip=True)
                for opt in sel.find_all("option")
                if opt.get_text(strip=True)
            ]
            if options:
                items.append(f"{name}: {', '.join(options[:8])}")
        # textareas
        for ta in soup.find_all("textarea"):
            name = ta.get("name") or ta.get("placeholder") or ta.get("id") or "textarea"
            items.append(f"textarea: {name}")
        # checkboxes
        for cb in soup.find_all("input", type="checkbox"):
            name = cb.get("name") or cb.get("id") or "checkbox"
            items.append(f"checkbox: {name}")
        return items[:20]  # cap
    except Exception:
        logger.exception("ui_extractor._extract_interactive failed")
        return []


def _build_content_summary(page_title: str, ui_elements: dict) -> str:
    """Build a compact plain-text summary of extracted UI elements.

    Used as the `content` field so callers that only look at `content` get
    a useful string representation.
    """
    lines: list[str] = []
    if page_title:
        lines.append(f"Page: {page_title}")
    headings = ui_elements.get("headings") or []
    if headings:
        lines.append("Headings: " + " | ".join(headings[:5]))
    fields = ui_elements.get("form_fields") or []
    if fields:
        field_strs = [
            f"{f.get('label') or f.get('name') or f.get('type')} ({f.get('type')})"
            for f in fields
        ]
        lines.append("Form fields: " + ", ".join(field_strs[:10]))
    buttons = ui_elements.get("buttons") or []
    if buttons:
        btn_strs = [b.get("text", "") for b in buttons if b.get("text")]
        lines.append("Buttons: " + ", ".join(btn_strs[:8]))
    nav = ui_elements.get("navigation_links") or []
    if nav:
        lines.append("Navigation: " + ", ".join(nav[:8]))
    interactive = ui_elements.get("interactive") or []
    if interactive and not fields and not buttons:
        lines.append("Notes: " + " | ".join(interactive[:5]))
    return "\n".join(lines)
