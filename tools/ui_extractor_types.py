"""Shared types for tools/ui_extractor.py: tier input/outcome and result dict."""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

logger = logging.getLogger("tools.ui_extractor")


class TierInput(NamedTuple):
    """What Tier 1/2 need; ``has_html`` is ``bool(fetch_result["raw_html"])``."""

    url: str
    raw_html: str
    page_title: str
    spa_shell: bool
    has_html: bool


class TierOutcome(NamedTuple):
    """Outcome of the tiers; ``screenshot`` is set only when deferred."""

    ui_elements: dict
    page_title: str
    method: str
    render_error: str | None
    screenshot: bytes | None = None


def _result(content: str, outcome: TierOutcome) -> dict:
    """Success dict; ``vision_screenshot`` is ABSENT unless one was deferred."""
    out: dict[str, Any] = {
        "ui_elements": outcome.ui_elements,
        "page_title": outcome.page_title,
        "content": content,
        "extraction_method": outcome.method,
        "render_error": outcome.render_error,
        "error": None,
    }
    if outcome.screenshot is not None:
        out["vision_screenshot"] = outcome.screenshot
    return out


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
