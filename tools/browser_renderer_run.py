"""Browser run for tools/browser_renderer.py: launch Chromium and render a job."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import NamedTuple

from tools.browser_renderer_page import (
    _MAX_HTML_CHARS,
    _accessibility_snapshot,
    _capture_screenshot,
    _merge_iframe_html,
    _navigate,
    _settle_hydration,
)


class RenderJob(NamedTuple):
    """What one render needs; ``redirect_failure`` is bound by the facade."""

    url: str
    capture_screenshot: bool
    playwright_error: type[BaseException]
    redirect_failure: Callable[[str, str], Awaitable[dict | None]]


async def _arrive(page, job: RenderJob) -> tuple[str, dict | None]:
    """Navigate, guard the redirect, settle; return ``(final_url, failure)``."""
    failure = await _navigate(page, job.url, job.playwright_error)
    if failure:
        return "", failure
    final_url = page.url
    failure = await job.redirect_failure(final_url, job.url)
    if failure:
        return final_url, failure
    await _settle_hydration(page, job.url, job.playwright_error)
    return final_url, None


async def _scrape(page, job: RenderJob, final_url: str) -> dict:
    """Collect html, title, iframes, screenshot and accessibility tree."""
    html = await page.content()
    title = await page.title()
    html = await _merge_iframe_html(page, html, job.url)
    screenshot_bytes = None
    if job.capture_screenshot:
        screenshot_bytes = await _capture_screenshot(
            page, job.url, job.playwright_error
        )
    accessibility = await _accessibility_snapshot(page, job.url)
    return {
        "error": None,
        "html": (html or "")[:_MAX_HTML_CHARS],
        "title": title or "",
        "screenshot": screenshot_bytes,
        "accessibility": accessibility,
        "final_url": final_url,
    }


async def _render_in_page(page, job: RenderJob) -> dict:
    """Navigate, guard the redirect, settle and scrape ``page``."""
    final_url, failure = await _arrive(page, job)
    if failure:
        return failure
    return await _scrape(page, job, final_url)


async def _launch_and_render(pw, resolver_rules: str, job: RenderJob) -> dict:
    """Launch Chromium with DNS pinned to the validated IP and render ``url``.

    Pinning stops Chromium re-resolving the hostname to a private/internal
    address after the SSRF check passed (DNS-rebinding bypass — NB-002).
    """
    resolver_arg = f"--host-resolver-rules={resolver_rules}"
    browser = await pw.chromium.launch(headless=True, args=[resolver_arg])
    try:
        return await _render_in_page(await browser.new_page(), job)
    finally:
        await browser.close()
