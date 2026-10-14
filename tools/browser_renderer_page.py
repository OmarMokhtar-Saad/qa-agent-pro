"""Page-level steps for tools/browser_renderer.py: navigate, settle, scrape."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise

logger = logging.getLogger("tools.browser_renderer")

_NAV_TIMEOUT_MS = 30_000
# After domcontentloaded fires, give a client-side framework (React/Vue/Angular)
# a bounded window to actually paint its interactive elements before we scrape.
# Non-fatal: on timeout we scrape whatever has rendered so far.
_HYDRATE_TIMEOUT_MS = 8_000
_MAX_HTML_CHARS = 200_000
_MAX_FRAMES = 10  # cap child (iframe) documents merged into the rendered HTML

_UNAVAILABLE_RESULT = {
    "html": None,
    "title": "",
    "screenshot": None,
    "accessibility": None,
    "final_url": None,
}


async def _frame_htmls(page) -> list[str]:
    """HTML of each reachable child frame (skips ones that refuse .content())."""
    child_frames = list(page.frames)[1:]  # [0] is the main frame
    frame_htmls: list[str] = []
    for fr in child_frames[:_MAX_FRAMES]:
        with never_raise() as g:
            fhtml = await fr.content()
        if g.failed:
            continue
        if fhtml:
            frame_htmls.append(fhtml)
    return frame_htmls


def _append_frames(html: str, frame_htmls: list[str], url: str) -> str:
    """Append the merged child-frame documents to ``html``."""
    logger.info(
        "render_page: merged %d iframe document(s) for %s",
        len(frame_htmls),
        url,
    )
    return (html or "") + "".join(
        f"\n<!-- iframe-content -->\n{fh}" for fh in frame_htmls
    )


# page.content() serializes ONLY the main frame, so a form embedded in an
# <iframe> (payment widgets, SSO, embedded signup) would be invisible to the
# BeautifulSoup extractors. Cross-origin or detached frames may refuse
# .content() — skip them individually. Never raises.
async def _merge_iframe_html(page, html: str, url: str) -> str:
    """Append each reachable child frame's HTML to ``html``."""
    with never_raise(
        logger,
        "render_page: iframe content collection skipped for %s",
        url,
        exc_info=True,
    ):
        frame_htmls = await _frame_htmls(page)
        if frame_htmls:
            html = _append_frames(html, frame_htmls, url)
    return html


async def _capture_screenshot(
    page, url: str, playwright_error: type[BaseException]
) -> bytes | None:
    """PNG screenshot of ``page``, or None when capture fails (non-fatal)."""
    try:
        return await page.screenshot(type="png")
    except playwright_error:
        logger.warning(
            "render_page: screenshot capture failed for %s (non-fatal)",
            url,
        )
        return None


async def _accessibility_snapshot(page, url: str) -> dict | None:
    """Accessibility tree of ``page``, or None when unavailable."""
    with never_raise(
        logger, "render_page: accessibility snapshot unavailable for %s", url
    ):
        return await page.accessibility.snapshot()
    return None


async def _navigate(
    page, url: str, playwright_error: type[BaseException]
) -> dict | None:
    """Load ``url``; return a failure result on navigation error, else None."""
    try:
        # domcontentloaded (not networkidle): a page that holds
        # telemetry/analytics sockets open (e.g. SauceDemo) never
        # reaches "networkidle", so that wait_until would burn the
        # whole nav timeout and fail even though the page rendered
        # fine. We wait for a real DOM instead, then settle for SPA
        # hydration afterwards.
        await page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
    except playwright_error as exc:
        logger.warning("render_page: navigation failed for %s: %s", url, exc)
        return {
            "error": f"Browser navigation failed: {exc}",
            **_UNAVAILABLE_RESULT,
        }
    return None


async def _settle_hydration(
    page, url: str, playwright_error: type[BaseException]
) -> None:
    """Wait, non-fatally, for the DOM to contain an interactive element."""
    # domcontentloaded fires before a client-side framework paints. On timeout
    # (e.g. a canvas-only UI) we fall through and scrape the current DOM;
    # Tier 3 vision can still take over.
    try:
        await page.wait_for_function(
            "() => document.querySelector("
            "'input, button, select, textarea, a, [role]') !== null",
            timeout=_HYDRATE_TIMEOUT_MS,
        )
    except playwright_error:
        logger.debug(
            "render_page: hydration wait timed out for %s — scraping current DOM",
            url,
        )
