"""UI Extractor -- parse live web pages into structured UI element data.

Uses a tiered fetch strategy so it works on both server-rendered pages and
JavaScript single-page apps (React/Vue/Angular, e.g. SauceDemo):

  Tier 1 -- tools/jira_fetcher.py (httpx). Fast, server-rendered pages. When it
            flags spa_shell=True (a JS-only shell) or its HTML yields zero UI
            elements, escalate to Tier 2. A result carrying NO raw_html (e.g. a
            Jira REST fetch, which returns ticket text) never escalates -- there
            is no HTML for a browser to re-render -- and reports "none".
  Tier 2 -- tools/browser_renderer.py (Playwright headless Chromium). Renders
            the page for real; the resulting HTML is parsed with the same
            BeautifulSoup extractors used for Tier 1. Degrades cleanly if
            Playwright/Chromium isn't installed.
  Tier 3 -- DELETED 2026-08-16 (dead-code deletion P2-F1). A rendered
            screenshot that still yields zero elements used to be described
            by llm.ask_vision() (api backend only). The only production
            caller passed defer_vision=True from a constant, so the call had
            been unreachable since 2026-08-12; the screenshot is returned
            under ``vision_screenshot`` and handed to the tester's OWN
            multimodal model through agents/host_mode.IMAGE_JOB instead.
            Ledger row `ui_extractor.describe_via_vision`.

Contract:
- Never raises -- always returns a dict.
- On success: {"ui_elements": {...}, "page_title": str, "content": str,
  "extraction_method": "static_html"|"js_rendered"|"vision_deferred"|
  "unavailable"|"none",
  "error": None}
- On failure: {"error": str, "content": None}
"""

from __future__ import annotations

import logging

from bs4 import BeautifulSoup

from tools.browser_renderer import render_page
from tools.jira_fetcher import fetch_url_content

logger = logging.getLogger(__name__)

# The Tier-3 ask_vision call, its _VISION_SYSTEM_PROMPT and the ledger id
# `ui_extractor.describe_via_vision` were DELETED on 2026-08-16 (P2-F1).
# extract_ui_elements itself is LIVE -- tools/mcp_handlers._ground_and_gate
# reaches it from qa_prepare_test_cases -- but its vision fallback was not:
# the only production caller passed defer_vision=True, derived from the
# hardcoded "host" generation mode, so the deferral branch is the only one
# that ever ran. The `defer_vision` parameter went with the branch it
# selected; deferral is now unconditional. The ledger id stays in
# tools/host_llm.LEDGER_IDS.


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

    When Tier 2 renders a page whose HTML still yields no elements, the
    screenshot is returned under ``vision_screenshot`` (raw bytes) with
    ``extraction_method="vision_deferred"`` so the caller can forward it to
    the tester's OWN multimodal model as MCP image content. ``ui_elements``
    stays empty on that branch. The ``vision_screenshot`` key is ABSENT (not
    None) unless a screenshot was actually produced.

    On any failure returns {"error": str, "content": None}.
    Never raises.
    """
    try:
        fetch_result = await _fetch_or_reuse(url, prefetched)
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
            return _result(
                {}, page_title, fetch_result.get("description") or "", "none", None
            )

        (
            ui_elements,
            page_title,
            extraction_method,
            render_error,
            deferred_screenshot,
        ) = await _run_tiers(url, fetch_result, raw_html, page_title, spa_shell)

        content_summary = _build_content_summary(page_title, ui_elements)
        if not content_summary:
            content_summary = fetch_result.get("description") or ""

        _log_extraction_counts(url, ui_elements, extraction_method)
        return _result(
            ui_elements,
            page_title,
            content_summary,
            extraction_method,
            render_error,
            deferred_screenshot,
        )
    except Exception as exc:
        logger.exception("ui_extractor: unexpected error for %s", url)
        return {"error": str(exc), "content": None}


async def _run_tiers(
    url: str, fetch_result: dict, raw_html: str, page_title: str, spa_shell: bool
) -> tuple[dict, str, str, str | None, bytes | None]:
    """Tier 1 parse, then Tier 2 when warranted.

    Returns (ui_elements, page_title, extraction_method, render_error,
    deferred_screenshot).
    """
    ui_elements = _parse_ui_elements(raw_html) if raw_html else {}
    # Tier 2 renders a page's HTML, so it can only help when HTML was
    # actually fetched. A Jira REST result carries the ticket's TEXT and no
    # HTML at all (raw_text is used as the raw_html fallback), so
    # _parse_ui_elements is always empty and _looks_empty escalated EVERY
    # Jira URL to a headless-Chromium render of an auth-walled issue/board
    # page (15.2s measured, and the board title overwrote the ticket summary).
    # Gating on raw_html fixes this and every future text-only source;
    # _fetch_generic returns raw_html on both of its 200 paths, so no real web
    # page loses Tier 2.
    # spa_shell stays an INDEPENDENT trigger: the fetcher setting it means
    # "this is a JS-only shell, re-render it", and that must hold even when
    # no HTML came back. Only the _looks_empty heuristic is gated on having
    # HTML -- that is the branch a text-only source wrongly satisfied.
    has_html = bool(fetch_result.get("raw_html"))
    if not spa_shell and not has_html:
        # No HTML was ever parsed, so "static_html" would misreport the
        # module contract; "none" is the documented value for that.
        logger.debug(
            "ui_extractor: %s returned no HTML (text-only source) -- "
            "skipping the Tier 2 browser render",
            url,
        )
        return ui_elements, page_title, "none", None, None
    if spa_shell or _looks_empty(ui_elements):
        logger.info(
            "ui_extractor: escalating to Tier 2 browser render for %s (spa_shell=%s)",
            url,
            spa_shell,
        )
        return await _escalate_to_tier2(url, ui_elements, page_title, "static_html")
    return ui_elements, page_title, "static_html", None, None


async def _fetch_or_reuse(url: str, prefetched: dict | None) -> dict:
    """Reuse a clean *prefetched* result, else fetch the URL."""
    if isinstance(prefetched, dict) and not prefetched.get("error"):
        return prefetched
    return await fetch_url_content(url)


def _log_extraction_counts(url: str, ui_elements: dict, extraction_method: str) -> None:
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


def _result(
    ui_elements: dict,
    page_title: str,
    content: str,
    extraction_method: str,
    render_error: str | None,
    deferred_screenshot: bytes | None = None,
) -> dict:
    """Success dict; ``vision_screenshot`` is ABSENT unless one was deferred."""
    out = {
        "ui_elements": ui_elements,
        "page_title": page_title,
        "content": content,
        "extraction_method": extraction_method,
        "render_error": render_error,
        "error": None,
    }
    if deferred_screenshot is not None:
        out["vision_screenshot"] = deferred_screenshot
    return out


async def _escalate_to_tier2(
    url: str, ui_elements: dict, page_title: str, extraction_method: str
) -> tuple[dict, str, str, str | None, bytes | None]:
    """Render *url* in a browser and fold the result into the Tier 1 outcome.

    Returns (ui_elements, page_title, extraction_method, render_error,
    deferred_screenshot).
    """
    render_error: str | None = None
    deferred_screenshot: bytes | None = None
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
        screenshot = rendered.get("screenshot") if not rendered.get("error") else None
        if screenshot:
            # Host-mode boomerang: make NO server-side vision call. The raw
            # screenshot rides to the host's OWN multimodal model as MCP
            # image content; ui_elements stays empty, which is the same
            # outcome cli/cursor already reached here.
            deferred_screenshot = screenshot
            extraction_method = "vision_deferred"
        else:
            extraction_method = "unavailable"
    return ui_elements, page_title, extraction_method, render_error, deferred_screenshot


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


# _describe_via_vision lived here until 2026-08-16 (P2-F1). It made the one
# llm.ask_vision call in this module and returned the raw result, including
# the "Error: ..." sentinel. Deleted with the branch that selected it.


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
            input_type = _field_input_type(tag)
            # Skip hidden and submit/button inputs -- they're captured elsewhere
            if input_type in ("hidden", "submit", "button", "image", "reset"):
                continue
            fields.append(
                {
                    "name": tag.get("name") or tag.get("id") or "",
                    "type": input_type,
                    "placeholder": tag.get("placeholder") or "",
                    "required": tag.has_attr("required"),
                    "label": _field_label(soup, tag),
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
