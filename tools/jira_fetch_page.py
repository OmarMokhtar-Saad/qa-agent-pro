"""Page-content half of the generic web fetcher: refusal checks, readable-text
extraction and the SPA-shell heuristic. Split out of ``tools/jira_fetcher.py``
(C5); that module re-exports every name here."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from tools.jira_fetch_hop import _HopResult

logger = logging.getLogger("tools.jira_fetcher")

_LOGIN_PATHS = ("/login", "/signin", "/auth", "id.atlassian.com", "accounts.google.com")

# At or below this many non-whitespace characters, extracted page text is treated
# as unusable (e.g. an auth wall or a JavaScript-rendered SPA that served an empty
# server-side body — the real failure was exactly 0 chars). We refuse rather than
# hand near-empty content to the generator. Kept low to avoid rejecting short but
# legitimate pages.
MIN_READABLE_CHARS = 20

_MAX_RAW_HTML_CHARS = 200_000  # cap raw HTML exposed to callers on very large pages

# Sentinels indicating a JS-only SPA shell (e.g. a React/Vue app that renders
# nothing without a browser) rather than a genuinely empty/broken page.
_JS_REQUIRED_SENTINELS = (
    "enable javascript",
    "you need to enable javascript",
    "please enable javascript",
    "javascript is required",
    "requires javascript",
)


# A body at/under this many chars is treated as a near-empty "shell" body — the
# regime where an "enable JavaScript" sentinel is genuine SPA evidence. A longer,
# content-rich body that merely *mentions* enabling JavaScript (a help article,
# a browser-support FAQ) is NOT an SPA shell, so the sentinel is ignored there.
_SPA_SHELL_BODY_CHARS = 200


def _has_js_sentinel(lowered_body: str, lowered_html: str) -> bool:
    """True when an "enable JavaScript" sentinel appears in the body or the HTML."""
    return any(
        sentinel in lowered_body or sentinel in lowered_html
        for sentinel in _JS_REQUIRED_SENTINELS
    )


# Heuristic SPA-shell detector. Never raises -- returns False on any
# inspection error.
#
# True only when the rendered body is a near-empty shell (<= _SPA_SHELL_BODY_CHARS)
# AND one of:
#   - an explicit "enable JavaScript" sentinel appears (in the short body or the
#     HTML) — e.g. SauceDemo's ~47-char noscript shell; or
#   - the page ships a sizeable inline/external script bundle with no real text.
#
# Gating the sentinel on a short body prevents a content-rich page whose copy
# merely mentions "please enable javascript" from being misflagged as an SPA
# shell (which would discard good text and needlessly escalate to the browser
# tier). Real content pages — which also contain <script> and may mention
# JavaScript — are therefore not misflagged.
def _looks_like_js_rendered(html: str, body_text: str) -> bool:
    """Heuristic SPA-shell detector; never raises, False on any error."""
    try:
        lowered_body = (body_text or "").strip().lower()
        lowered_html = (html or "").lower()
        # A content-rich body is never an SPA shell, regardless of what it mentions.
        if len(lowered_body) > _SPA_SHELL_BODY_CHARS:
            return False
        # Near-empty body: an explicit sentinel (in the short body or the raw
        # HTML/noscript) => JS-only shell. SauceDemo's shell body stays short.
        if _has_js_sentinel(lowered_body, lowered_html):
            return True
        # Or a near-empty body that ships a script bundle qualifies.
        return "<script" in lowered_html and len(lowered_html) > 1000
    except Exception:
        logger.exception("_looks_like_js_rendered: heuristic check failed")
        return False


def _http_refusal(resp: _HopResult, final_url: str) -> dict | None:
    """Refuse an auth wall, an HTTP error or a login redirect; ``None`` means carry on."""
    if resp.status_code in (401, 403):
        return {
            "error": f"HTTP {resp.status_code}: page requires authentication",
            "content": None,
        }

    if resp.status_code >= 400:
        return {
            "error": f"HTTP {resp.status_code}: request failed",
            "content": None,
        }

    if any(p in final_url for p in _LOGIN_PATHS):
        return {
            "error": "Redirected to login page — page requires authentication",
            "content": None,
        }
    return None


def _extract_title_and_text(html: str) -> tuple[str, str]:
    """Return (title, readable body text) of an HTML page."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()

    title_tag = soup.find("title")
    title_text = title_tag.get_text(strip=True) if title_tag else ""

    main = soup.find("main") or soup.find("article") or soup.find("body")
    body_text = (
        main.get_text(separator="\n", strip=True)
        if main
        else soup.get_text(separator="\n", strip=True)
    )
    return title_text, body_text


def _unreadable_page_result() -> dict:
    """The refusal for a 200 page with no readable text."""
    return {
        "error": (
            "Could not extract readable content from the page — it likely "
            "requires authentication or is rendered with JavaScript (e.g. a "
            "Jira or other single-page app). Please paste the ticket text and "
            "I'll generate test cases from it."
        ),
        "content": None,
    }


def _spa_shell_result(title_text: str, body_text: str, html: str) -> dict:
    """The result flagged ``spa_shell`` for browser-render escalation."""
    return {
        "title": title_text,
        "description": "",
        "acceptance_criteria": "",
        "raw_text": body_text[:5000],
        "raw_html": html[:_MAX_RAW_HTML_CHARS],
        "content": "",
        "spa_shell": True,
        "error": None,
    }


def _readable_result(title_text: str, body_text: str, html: str) -> dict:
    """The result for a page with readable text."""
    return {
        "title": title_text,
        "description": body_text[:3000],
        "acceptance_criteria": "",
        "raw_text": body_text[:5000],
        "raw_html": html[:_MAX_RAW_HTML_CHARS],
        "content": body_text[:5000],
        "spa_shell": False,
        "error": None,
    }


# A JS-only SPA shell (e.g. a React/Vue app like SauceDemo) may serve a
# short "enable JavaScript" message that is OVER MIN_READABLE_CHARS, so
# detect it independently of the readable-text gate and flag spa_shell so
# tools/ui_extractor.py can escalate to a Tier-2 browser render.
# Otherwise a 200 with no readable text means an auth wall or a genuinely
# broken/empty page — refuse rather than return empty content the
# generator would fabricate cases from.
def _page_result(url: str, resp: _HopResult) -> dict:
    """Extract the readable page content from a good response into the result dict."""
    title_text, body_text = _extract_title_and_text(resp.text)
    if _looks_like_js_rendered(resp.text, body_text):
        logger.info(
            "Detected JS-rendered SPA shell for %s — flagging spa_shell for "
            "browser-render escalation",
            url,
        )
        return _spa_shell_result(title_text, body_text, resp.text)
    if len(body_text.strip()) < MIN_READABLE_CHARS:
        return _unreadable_page_result()
    return _readable_result(title_text, body_text, resp.text)
