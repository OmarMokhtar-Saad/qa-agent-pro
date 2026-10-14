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
            multimodal model through agents/host_image.IMAGE_JOB instead.
            Ledger row `ui_extractor.describe_via_vision`.

Contract:
- Never raises -- always returns a dict.
- On success: {"ui_elements": {...}, "page_title": str, "content": str,
  "extraction_method": "static_html"|"js_rendered"|"vision_deferred"|
  "unavailable"|"none",
  "error": None}
- On failure: {"error": str, "content": None}

extract_ui_elements() success dict:
  ui_elements (dict) -- structured element data grouped by category
  page_title  (str)  -- <title> text
  content     (str)  -- plain-text summary used as fallback context
  extraction_method (str) -- which tier produced the result
  error       (None) -- always None on success

A non-None, error-free *prefetched* result (B-008/D-3) is reused instead of
fetching the URL a second time; see ``_fetch_or_reuse``.

When Tier 2 renders a page whose HTML still yields no elements, the
screenshot is returned under ``vision_screenshot`` (raw bytes) with
``extraction_method="vision_deferred"`` so the caller can forward it to
the tester's OWN multimodal model as MCP image content. ``ui_elements``
stays empty on that branch. The ``vision_screenshot`` key is ABSENT (not
None) unless a screenshot was actually produced.
"""

from __future__ import annotations

import logging

from tools.browser_renderer import render_page
from tools.jira_fetcher import fetch_url_content
from tools.ui_extractor_fields import (  # noqa: F401
    _extract_form_fields,
    _extract_interactive,
)
from tools.ui_extractor_modal import (  # noqa: F401
    _field_modal_trigger,
    _find_modal_triggers,
)
from tools.ui_extractor_parse import (  # noqa: F401
    _extract_buttons,
    _extract_headings,
    _extract_nav_links,
    _parse_ui_elements,
)
from tools.ui_extractor_summary import _build_content_summary
from tools.ui_extractor_tiers import _looks_empty, _run_tiers  # noqa: F401
from tools.ui_extractor_types import (
    TierInput,
    TierOutcome,
    _log_extraction_counts,
    _result,
)

logger = logging.getLogger(__name__)

# The Tier-3 ask_vision call, its _VISION_SYSTEM_PROMPT and the ledger id
# `ui_extractor.describe_via_vision` were DELETED on 2026-08-16 (P2-F1).
# extract_ui_elements itself is LIVE -- handlers.ground_and_gate
# reaches it from qa_prepare_test_cases -- but its vision fallback was not:
# the only production caller passed defer_vision=True, derived from the
# hardcoded "host" generation mode, so the deferral branch is the only one
# that ever ran. The `defer_vision` parameter went with the branch it
# selected; deferral is now unconditional. The ledger id stays in
# tools/host_llm.LEDGER_IDS.
# _describe_via_vision lived here until 2026-08-16 (P2-F1). It made the one
# llm.ask_vision call in this module and returned the raw result, including
# the "Error: ..." sentinel. Deleted with the branch that selected it.


def _fetch_error_result(url: str, fetch_result: dict) -> dict:
    """Failure dict for a fetch that returned an error."""
    logger.warning(
        "ui_extractor: fetch_url_content failed for %s: %s",
        url,
        fetch_result["error"],
    )
    return {"error": fetch_result["error"], "content": None}


async def extract_ui_elements(url: str, prefetched: dict | None = None) -> dict:
    """Fetch a live URL and extract structured UI elements from its HTML.

    Result shape: see the module docstring. On any failure returns
    {"error": str, "content": None}. Never raises.
    """
    try:
        fetch_result = await _fetch_or_reuse(url, prefetched)
        if fetch_result.get("error"):
            return _fetch_error_result(url, fetch_result)
        return await _extract_from_fetch(url, fetch_result)
    except Exception as exc:
        logger.exception("ui_extractor: unexpected error for %s", url)
        return {"error": str(exc), "content": None}


def _raw_html_of(fetch_result: dict) -> str:
    """The fetched HTML, falling back to the raw text, then the content."""
    return (
        fetch_result.get("raw_html")
        or fetch_result.get("raw_text")
        or fetch_result.get("content")
        or ""
    )


def _no_html_result(url: str, page_title: str, fetch_result: dict) -> dict:
    """Text fallback when no HTML came back and the page is not an SPA shell."""
    logger.warning(
        "ui_extractor: no HTML content returned for %s -- returning text fallback",
        url,
    )
    description = fetch_result.get("description") or ""
    return _result(description, TierOutcome({}, page_title, "none", None))


async def _extract_from_fetch(url: str, fetch_result: dict) -> dict:
    """Build the success dict from a clean fetch result."""
    raw_html = _raw_html_of(fetch_result)
    page_title = fetch_result.get("title") or ""
    spa_shell = bool(fetch_result.get("spa_shell"))
    if not raw_html and not spa_shell:
        return _no_html_result(url, page_title, fetch_result)
    has_html = bool(fetch_result.get("raw_html"))
    src = TierInput(url, raw_html, page_title, spa_shell, has_html)
    outcome = await _run_tiers(src, render_page)
    content_summary = _build_content_summary(outcome.page_title, outcome.ui_elements)
    content_summary = content_summary or fetch_result.get("description") or ""
    _log_extraction_counts(url, outcome.ui_elements, outcome.method)
    return _result(content_summary, outcome)


async def _fetch_or_reuse(url: str, prefetched: dict | None) -> dict:
    """Reuse a clean *prefetched* result, else fetch the URL.

    A non-None, error-free result of a prior ``fetch_url_content(url)`` is
    reused (B-008/D-3); None or an error-bearing one falls back to the fetch.
    """
    if isinstance(prefetched, dict) and not prefetched.get("error"):
        return prefetched
    return await fetch_url_content(url)
