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
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

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


def _host_resolver_rules(hostname: str, validated_ip: str) -> str:
    """Build a Chromium ``--host-resolver-rules`` value pinning `hostname`
    (and its :443 form) to the already-validated public IP.

    This forces Chromium to reuse the IP the SSRF guard checked instead of
    performing its own second DNS lookup, so a DNS-rebinding attacker cannot
    answer "public" for the guard and "169.254.169.254 / 10.x / 127.0.0.1" for
    Chromium moments later (NB-002).
    """
    return (
        f"MAP {hostname} {validated_ip},"
        f"MAP {hostname}:443 {validated_ip}:443,"
        f"MAP {hostname}:80 {validated_ip}:80"
    )


async def _validate_public_host(url: str) -> tuple[str | None, str | None, str | None]:
    """Validate `url` as a public http(s) target and pin its resolved IP.

    Returns ``(hostname, validated_ip, error)`` — ``error`` is None on success,
    in which case ``hostname``/``validated_ip`` are set. On failure the error
    string is set and the other two are None. Mirrors the SSRF/private-address
    guard in tools/jira_fetcher.py (every resolved address must be global) and
    additionally hands back the FIRST validated public IP so render_page can
    pin Chromium's DNS to it (``--host-resolver-rules``), closing the
    DNS-rebinding window where Chromium re-resolves to a private address after
    the check passed (NB-002). Never raises.
    """
    try:
        import asyncio

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return None, None, f"Blocked: scheme '{parsed.scheme}' not allowed"
        hostname = parsed.hostname
        if not hostname:
            return None, None, "Blocked: invalid hostname"
        try:
            loop = asyncio.get_running_loop()
            infos = await loop.getaddrinfo(hostname, None)
        except socket.gaierror:
            return None, None, f"Blocked: could not resolve hostname '{hostname}'"
        validated_ip: str | None = None
        for info in infos:
            addr = ipaddress.ip_address(info[4][0])
            if not addr.is_global:
                return None, None, "Blocked: non-public address"
            if validated_ip is None:
                validated_ip = info[4][0]
        if validated_ip is None:
            return None, None, "Blocked: non-public address"
        return hostname, validated_ip, None
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


async def render_page(url: str, capture_screenshot: bool = True) -> dict:
    """Render `url` in headless Chromium and return the fully rendered HTML.

    Returns on success:
      {"error": None, "html": str, "title": str, "screenshot": bytes | None,
       "accessibility": dict | None, "final_url": str}
    Returns on failure (blocked URL, Playwright not installed, Chromium not
    installed, navigation timeout, or any other browser error):
      {"error": str, "html": None, "title": "", "screenshot": None,
       "accessibility": None, "final_url": None}

    Never raises.
    """
    # SSRF guard: validate the target BEFORE spinning up a browser, and capture
    # the validated public IP so Chromium's own DNS can be pinned to it.
    hostname, validated_ip, block_reason = await _validate_public_host(url)
    if block_reason:
        logger.warning("render_page: refusing to render %s — %s", url, block_reason)
        return {"error": block_reason, **_UNAVAILABLE_RESULT}

    try:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning(
            "render_page: playwright is not installed -- install the 'browser' "
            "extra (`pip install -e '.[browser]'` then `playwright install "
            "chromium`) to enable JS-rendered SPA support."
        )
        return {
            "error": (
                "Browser rendering is not available (Playwright is not "
                "installed). Install it with `pip install -e '.[browser]'` and "
                "run `playwright install chromium`."
            ),
            **_UNAVAILABLE_RESULT,
        }

    try:
        async with async_playwright() as pw:
            # Pin Chromium's DNS to the IP the SSRF guard already validated so it
            # cannot re-resolve `hostname` to a private/internal address after
            # the check passed (DNS-rebinding / SSRF bypass — NB-002).
            resolver_arg = (
                f"--host-resolver-rules={_host_resolver_rules(hostname, validated_ip)}"
            )
            browser = await pw.chromium.launch(headless=True, args=[resolver_arg])
            try:
                page = await browser.new_page()
                try:
                    # domcontentloaded (not networkidle): a page that holds
                    # telemetry/analytics sockets open (e.g. SauceDemo) never
                    # reaches "networkidle", so that wait_until would burn the
                    # whole nav timeout and fail even though the page rendered
                    # fine. We wait for a real DOM instead, then settle for SPA
                    # hydration below.
                    await page.goto(
                        url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS
                    )
                except PlaywrightError as exc:
                    logger.warning(
                        "render_page: navigation failed for %s: %s", url, exc
                    )
                    return {
                        "error": f"Browser navigation failed: {exc}",
                        **_UNAVAILABLE_RESULT,
                    }

                # SSRF guard: re-check the post-redirect final URL. A page that
                # redirected to a private/internal address must be discarded.
                final_url = page.url
                redirect_block = await _public_host_error(final_url)
                if redirect_block:
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

                # SPA hydration settle: domcontentloaded fires before a
                # client-side framework paints, so wait — non-fatally — for the
                # DOM to actually contain an interactive element before we
                # scrape it. On timeout (e.g. a canvas-only UI) we fall through
                # and scrape the current DOM; Tier 3 vision can still take over.
                try:
                    await page.wait_for_function(
                        "() => document.querySelector("
                        "'input, button, select, textarea, a, [role]') !== null",
                        timeout=_HYDRATE_TIMEOUT_MS,
                    )
                except PlaywrightError:
                    logger.debug(
                        "render_page: hydration wait timed out for %s — "
                        "scraping current DOM",
                        url,
                    )

                html = await page.content()
                title = await page.title()

                # Also pull HTML from child frames (iframes). page.content()
                # serializes ONLY the main frame, so a form embedded in an
                # <iframe> (payment widgets, SSO, embedded signup) would be
                # invisible to the BeautifulSoup extractors. Append each reachable
                # frame's HTML so those fields are captured too. Cross-origin or
                # detached frames may refuse .content() — skip them individually.
                try:
                    child_frames = list(page.frames)[1:]  # [0] is the main frame
                    frame_htmls: list[str] = []
                    for fr in child_frames[:_MAX_FRAMES]:
                        try:
                            fhtml = await fr.content()
                        except Exception:
                            continue
                        if fhtml:
                            frame_htmls.append(fhtml)
                    if frame_htmls:
                        logger.info(
                            "render_page: merged %d iframe document(s) for %s",
                            len(frame_htmls),
                            url,
                        )
                        html = (html or "") + "".join(
                            f"\n<!-- iframe-content -->\n{fh}" for fh in frame_htmls
                        )
                except Exception:
                    logger.debug(
                        "render_page: iframe content collection skipped for %s",
                        url,
                        exc_info=True,
                    )

                screenshot_bytes = None
                if capture_screenshot:
                    try:
                        screenshot_bytes = await page.screenshot(type="png")
                    except PlaywrightError:
                        logger.warning(
                            "render_page: screenshot capture failed for %s (non-fatal)",
                            url,
                        )

                accessibility = None
                try:
                    accessibility = await page.accessibility.snapshot()
                except Exception:
                    logger.debug(
                        "render_page: accessibility snapshot unavailable for %s",
                        url,
                    )

                return {
                    "error": None,
                    "html": (html or "")[:_MAX_HTML_CHARS],
                    "title": title or "",
                    "screenshot": screenshot_bytes,
                    "accessibility": accessibility,
                    "final_url": final_url,
                }
            finally:
                await browser.close()
    except Exception as exc:
        logger.exception("render_page: unexpected browser error for %s", url)
        return {"error": f"Browser rendering failed: {exc}", **_UNAVAILABLE_RESULT}
