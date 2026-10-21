"""Tier 1 parse and Tier 2 browser escalation for tools/ui_extractor.py.

Gating (moved from ``_run_tiers``):

Tier 2 renders a page's HTML, so it can only help when HTML was actually
fetched. A Jira REST result carries the ticket's TEXT and no HTML at all
(raw_text is used as the raw_html fallback), so _parse_ui_elements is always
empty and _looks_empty escalated EVERY Jira URL to a headless-Chromium render
of an auth-walled issue/board page (15.2s measured, and the board title
overwrote the ticket summary). Gating on raw_html fixes this and every future
text-only source; _fetch_generic returns raw_html on both of its 200 paths, so
no real web page loses Tier 2.

spa_shell stays an INDEPENDENT trigger: the fetcher setting it means "this is
a JS-only shell, re-render it", and that must hold even when no HTML came
back. Only the _looks_empty heuristic is gated on having HTML -- that is the
branch a text-only source wrongly satisfied.

Host-mode boomerang: when a rendered page still yields no elements, make NO
server-side vision call. The raw screenshot rides to the host's OWN
multimodal model (host-supplied only; this server sends no images); ui_elements stays empty, which is the
same outcome cli/cursor already reached here.
"""

from __future__ import annotations

import logging

from tools.ui_extractor_parse import _parse_ui_elements
from tools.ui_extractor_types import TierInput, TierOutcome

logger = logging.getLogger("tools.ui_extractor")


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


def _fold_render(url: str, first: TierOutcome, rendered: dict) -> TierOutcome:
    """Fold a ``render_page`` result into the Tier 1 outcome."""
    if rendered.get("error"):
        logger.warning(
            "ui_extractor: Tier 2 browser render unavailable/failed for %s: %s",
            url,
            rendered["error"],
        )
        return first._replace(render_error=rendered["error"])
    rendered_html = rendered.get("html") or ""
    if not rendered_html:
        return first
    elements = _parse_ui_elements(rendered_html)
    if _looks_empty(elements):
        return first
    title = rendered["title"] if rendered.get("title") else first.page_title
    return first._replace(ui_elements=elements, method="js_rendered", page_title=title)


def _defer_or_unavailable(out: TierOutcome, rendered: dict) -> TierOutcome:
    """When still empty: defer the screenshot to the host, else unavailable."""
    if not _looks_empty(out.ui_elements):
        return out
    screenshot = rendered.get("screenshot") if not rendered.get("error") else None
    if screenshot:
        return out._replace(method="vision_deferred", screenshot=screenshot)
    return out._replace(method="unavailable")


async def _escalate_to_tier2(src: TierInput, first: TierOutcome, render) -> TierOutcome:
    """Render *src.url* in a browser and fold the result into the Tier 1 outcome."""
    rendered = await render(src.url)
    return _defer_or_unavailable(_fold_render(src.url, first, rendered), rendered)


def _log_skip(url: str) -> None:
    logger.debug(
        "ui_extractor: %s returned no HTML (text-only source) -- "
        "skipping the Tier 2 browser render",
        url,
    )


def _log_escalate(url: str, spa_shell: bool) -> None:
    logger.info(
        "ui_extractor: escalating to Tier 2 browser render for %s (spa_shell=%s)",
        url,
        spa_shell,
    )


async def _run_tiers(src: TierInput, render) -> TierOutcome:
    """Tier 1 parse, then Tier 2 (via the ``render`` coroutine) when warranted."""
    ui_elements = _parse_ui_elements(src.raw_html) if src.raw_html else {}
    first = TierOutcome(ui_elements, src.page_title, "static_html", None)
    if not src.spa_shell and not src.has_html:
        # No HTML was ever parsed, so "static_html" would misreport the
        # module contract; "none" is the documented value for that.
        _log_skip(src.url)
        return first._replace(method="none")
    if src.spa_shell or _looks_empty(ui_elements):
        _log_escalate(src.url, src.spa_shell)
        return await _escalate_to_tier2(src, first, render)
    return first
