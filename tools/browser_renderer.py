"""Browser-rendering tier for JavaScript-heavy single-page apps (Tier 2 + Tier 3).

tools/jira_fetcher.py (Tier 1, httpx) cannot execute JavaScript, so a React/Vue/
Angular SPA (e.g. SauceDemo) serves a near-empty body and fetch_url_content()
flags it via ``spa_shell=True``. tools/ui_extractor.py then escalates here:

  Tier 2 -- render_page(): headless Chromium via Playwright renders the page,
           returns the fully rendered HTML (parseable by the existing
           BeautifulSoup extractors) plus a best-effort accessibility tree
           and a PNG screenshot for Tier 3.
  Tier 3 -- the screenshot is handed to llm.ask_vision() by tools/ui_extractor.py
           when Tier 2's rendered HTML still yields no elements (e.g. a
           canvas-only UI).

Contract:
- Never raises -- every public function returns a dict with an "error" key
  (None on success).
- SSRF-safe -- the target URL is re-validated against the same private-address
  guard tools/jira_fetcher.py applies BEFORE the browser navigates, and the
  post-redirect final URL is re-checked AFTER navigation. A page that redirects
  to a private/internal address is discarded with an error, so the browser tier
  cannot be used to reach internal services the httpx tier would have blocked.
- Playwright is an OPTIONAL dependency (see the ``browser`` extra in
  pyproject.toml). When it isn't installed, or Chromium hasn't been
  downloaded (``playwright install chromium``), render_page() returns a
  clean error dict instead of crashing the caller -- Tier 1 (httpx) results
  remain usable on their own.

render_page() returns on success:
  {"error": None, "html": str, "title": str, "screenshot": bytes | None,
   "accessibility": dict | None, "final_url": str}
and on failure (blocked URL, Playwright not installed, Chromium not installed,
navigation timeout, or any other browser error):
  {"error": str, "html": None, "title": "", "screenshot": None,
   "accessibility": None, "final_url": None}
"""

from __future__ import annotations

import logging
from typing import cast

from tools.browser_renderer_host import (  # noqa: F401
    _host_resolver_rules,
    _parse_target,
    pin_public_ip,
)
from tools.browser_renderer_page import (  # noqa: F401
    _HYDRATE_TIMEOUT_MS,
    _MAX_FRAMES,
    _MAX_HTML_CHARS,
    _NAV_TIMEOUT_MS,
    _UNAVAILABLE_RESULT,
    _accessibility_snapshot,
    _capture_screenshot,
    _merge_iframe_html,
    _navigate,
    _settle_hydration,
)
from tools.browser_renderer_run import (  # noqa: F401
    RenderJob,
    _launch_and_render,
    _render_in_page,
)
from tools.net_guard import embedded_v4_non_public  # noqa: F401

logger = logging.getLogger(__name__)

_PLAYWRIGHT_MISSING = False  # latched True after the first failed import


# Validate `url` as a public http(s) target and pin its resolved IP.
#
# Returns ``(hostname, validated_ip, error)`` — ``error`` is None on success,
# in which case ``hostname``/``validated_ip`` are set. On failure the error
# string is set and the other two are None. Mirrors the SSRF/private-address
# guard in tools/jira_fetcher.py (every resolved address must be global) and
# additionally hands back the FIRST validated public IP so render_page can
# pin Chromium's DNS to it (``--host-resolver-rules``), closing the
# DNS-rebinding window where Chromium re-resolves to a private address after
# the check passed (NB-002). Never raises.
async def validate_public_host(url: str) -> tuple[str | None, str | None, str | None]:
    """Validate `url` as a public http(s) target and pin its resolved IP."""
    try:
        hostname, error = _parse_target(url)
        if error:
            return None, None, error
        ip, error = await pin_public_ip(cast("str", hostname))
        if error:
            return None, None, error
        return hostname, ip, None
    except Exception:
        # Fail closed: any unexpected error inspecting the URL blocks the render.
        logger.exception("_validate_public_host: URL validation failed for %s", url)
        return None, None, "Blocked: URL validation error"


async def _public_host_error(url: str) -> str | None:
    """Return an error string if `url` is not a public http(s) target, else None.

    Thin wrapper over _validate_public_host preserved for callers/tests that
    only need the block reason (e.g. the post-navigation final-URL re-check).
    Never raises.
    """
    _hostname, _ip, error = await _validate_public_host(url)
    return error


def _playwright_missing_result() -> dict:
    """Failure result for an environment without Playwright."""
    return {
        "error": (
            "Browser rendering is not available (Playwright is not "
            "installed). Install it with `pip install -e '.[browser]'` and "
            "run `playwright install chromium`."
        ),
        **_UNAVAILABLE_RESULT,
    }


async def _redirect_failure(final_url: str, url: str) -> dict | None:
    """SSRF guard: failure result when the post-redirect URL is blocked."""
    # A page that redirected to a private/internal address must be discarded.
    redirect_block = await _public_host_error(final_url)
    if not redirect_block:
        return None
    logger.warning(
        "render_page: %s redirected to blocked target %s — %s",
        url,
        final_url,
        redirect_block,
    )
    return {
        "error": f"Blocked after redirect: {redirect_block}",
        **_UNAVAILABLE_RESULT,
    }


def playwright_missing() -> bool:
    """True once a playwright import failed (the render latch)."""
    return _PLAYWRIGHT_MISSING


def _load_playwright():
    """``(Error, async_playwright)``, or None (and latch) when not installed."""
    global _PLAYWRIGHT_MISSING
    try:
        # Kept lazy: playwright is heavy and optional (not installed in most venvs).
        from playwright.async_api import Error, async_playwright
    except ImportError:
        _PLAYWRIGHT_MISSING = True
        logger.warning(
            "render_page: playwright is not installed -- install the 'browser' "
            "extra (`pip install -e '.[browser]'` then `playwright install "
            "chromium`) to enable JS-rendered SPA support."
        )
        return None
    return Error, async_playwright


async def _render_checked(url: str, capture_screenshot: bool, rules: str) -> dict:
    """Render a validated target; never raises."""
    loaded = _load_playwright()
    if loaded is None:
        return _playwright_missing_result()
    playwright_error, async_playwright = loaded
    job = RenderJob(url, capture_screenshot, playwright_error, _redirect_failure)
    try:
        async with async_playwright() as pw:
            return await _launch_and_render(pw, rules, job)
    except Exception as exc:
        logger.exception("render_page: unexpected browser error for %s", url)
        return {"error": f"Browser rendering failed: {exc}", **_UNAVAILABLE_RESULT}


async def render_page(url: str, capture_screenshot: bool = True) -> dict:
    """Render `url` in headless Chromium and return the fully rendered HTML.

    Result shape: see the module docstring. Never raises.
    """
    # SSRF guard: validate the target BEFORE spinning up a browser, and capture
    # the validated public IP so Chromium's own DNS can be pinned to it.
    if playwright_missing():
        logger.debug("render_page: playwright known missing — skipping render")
        return _playwright_missing_result()
    hostname, validated_ip, block_reason = await _validate_public_host(url)
    if block_reason:
        logger.warning("render_page: refusing to render %s — %s", url, block_reason)
        return {"error": block_reason, **_UNAVAILABLE_RESULT}
    rules = _host_resolver_rules(cast("str", hostname), cast("str", validated_ip))
    return await _render_checked(url, capture_screenshot, rules)


async def _validate_public_host(url: str) -> tuple[str | None, str | None, str | None]:
    """Legacy name of :func:`validate_public_host` (WX removes it)."""
    return await validate_public_host(url)
