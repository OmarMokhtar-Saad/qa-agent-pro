from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from config.settings import settings

logger = logging.getLogger(__name__)

_RETRY_MAX = 2
_RETRY_DELAYS = (1.0, 2.0)  # seconds between attempts

# At or below this many non-whitespace characters, extracted page text is treated
# as unusable (e.g. an auth wall or a JavaScript-rendered SPA that served an empty
# server-side body — the real failure was exactly 0 chars). We refuse rather than
# hand near-empty content to the generator. Kept low to avoid rejecting short but
# legitimate pages.
_MIN_READABLE_CHARS = 20

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


def _looks_like_js_rendered(html: str, body_text: str) -> bool:
    """Heuristic SPA-shell detector. Never raises -- returns False on any
    inspection error.

    True only when the rendered body is a near-empty shell (<= _SPA_SHELL_BODY_CHARS)
    AND one of:
      - an explicit "enable JavaScript" sentinel appears (in the short body or the
        HTML) — e.g. SauceDemo's ~47-char noscript shell; or
      - the page ships a sizeable inline/external script bundle with no real text.

    Gating the sentinel on a short body prevents a content-rich page whose copy
    merely mentions "please enable javascript" from being misflagged as an SPA
    shell (which would discard good text and needlessly escalate to the browser
    tier). Real content pages — which also contain <script> and may mention
    JavaScript — are therefore not misflagged.
    """
    try:
        lowered_body = (body_text or "").strip().lower()
        lowered_html = (html or "").lower()
        # A content-rich body is never an SPA shell, regardless of what it mentions.
        if len(lowered_body) > _SPA_SHELL_BODY_CHARS:
            return False
        # Near-empty body: an explicit sentinel (in the short body or the raw
        # HTML/noscript) => JS-only shell. SauceDemo's shell body stays short.
        if any(
            sentinel in lowered_body or sentinel in lowered_html
            for sentinel in _JS_REQUIRED_SENTINELS
        ):
            return True
        # Or a near-empty body that ships a script bundle qualifies.
        return "<script" in lowered_html and len(lowered_html) > 1000
    except Exception:
        logger.exception("_looks_like_js_rendered: heuristic check failed")
        return False


async def fetch_url_content(url: str) -> dict:
    """Fetch content from a Jira ticket or generic URL. Never raises — returns error dict on failure."""
    try:
        parsed = urlparse(url)

        hostname, _resolved_ip, block_error = await _validate_public_url(url)
        if block_error:
            return {"error": block_error, "content": None}

        jira_hostname = (
            urlparse(settings.jira_base_url).hostname
            if settings.jira_base_url
            else None
        )
        is_jira = (
            jira_hostname
            and hostname == jira_hostname
            and ("/browse/" in parsed.path or "/issues/" in parsed.path)
        )

        if is_jira:
            return await _fetch_jira(parsed.path)
        return await _fetch_generic(url)

    except Exception as exc:
        return {"error": str(exc), "content": None}


def _extract_priority(fields: dict) -> str:
    """Priority is a {name: "High", ...} object, or absent on some issue types."""
    priority = fields.get("priority")
    if isinstance(priority, dict):
        return priority.get("name", "") or ""
    return ""


def _extract_names(items: object) -> list[str]:
    """Extract display names from a Jira field that's a list of either plain
    strings (labels) or {name: ...} objects (components, versions). Never raises."""
    if not isinstance(items, list):
        return []
    names: list[str] = []
    for item in items:
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
        elif isinstance(item, dict) and item.get("name"):
            names.append(item["name"])
    return names


async def _fetch_jira_comments(key: str) -> list[str]:
    """Fetch up to settings.jira_max_comments comment bodies (newest first).

    Comments are supplementary context, not required for generation — never
    raises, returns [] on any failure or when disabled via settings.
    """
    if not settings.jira_fetch_comments or settings.jira_max_comments <= 0:
        return []
    try:
        url = f"{settings.jira_base_url.rstrip('/')}/rest/api/3/issue/{key}/comment"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                url,
                auth=(settings.jira_email, settings.jira_api_token),
                params={
                    "maxResults": settings.jira_max_comments,
                    "orderBy": "-created",
                },
            )
        if resp.status_code != 200:
            logger.warning(
                "Jira comments fetch HTTP %d for %s — continuing without comments",
                resp.status_code,
                key,
            )
            return []
        data = resp.json()
        raw_comments = data.get("comments", [])[: settings.jira_max_comments]
        out: list[str] = []
        for c in raw_comments:
            body = c.get("body", "") or ""
            if isinstance(body, dict):
                body = _extract_adf_text(body)
            author = (c.get("author") or {}).get("displayName", "Unknown")
            body = body.strip()
            if body:
                out.append(f"{author}: {body}")
        return out
    except Exception:
        logger.exception(
            "Jira comments fetch failed for %s — continuing without comments", key
        )
        return []


def _extract_image_attachments(fields: dict) -> list[dict]:
    """Return up to settings.jira_max_images image attachments as
    [{filename, mime, url, size}], skipping non-image and oversized files.
    Never raises."""
    if not settings.jira_fetch_images or settings.jira_max_images <= 0:
        return []
    try:
        attachments = fields.get("attachment", [])
        if not isinstance(attachments, list):
            return []
        out: list[dict] = []
        for att in attachments:
            mime = (att.get("mimeType") or "").lower()
            if not mime.startswith("image/"):
                continue
            size = att.get("size") or 0
            if size and size > settings.jira_max_image_bytes:
                logger.info(
                    "Skipping oversized Jira image attachment '%s' (%d bytes)",
                    att.get("filename", "?"),
                    size,
                )
                continue
            url = att.get("content", "")
            if not url:
                continue
            out.append(
                {
                    "filename": att.get("filename", "attachment"),
                    "mime": mime,
                    "url": url,
                    "size": size,
                }
            )
            if len(out) >= settings.jira_max_images:
                break
        return out
    except Exception:
        logger.exception("Extracting Jira image attachments failed")
        return []


async def _download_jira_attachment(url: str) -> bytes | None:
    """Download an authenticated Jira attachment's raw bytes.

    Refuses any URL not on the configured Jira host — defense in depth so a
    malicious/compromised API response could never redirect this call's Basic
    Auth credentials to an attacker-controlled host (mirrors the SSRF
    discipline the rest of this module applies to generic URL fetches).
    Redirects are disabled outright for the same reason. Never raises —
    returns None on any failure, oversized body, or host mismatch.
    """
    try:
        jira_host = urlparse(settings.jira_base_url).hostname
        if not jira_host or urlparse(url).hostname != jira_host:
            logger.warning(
                "Refusing to download Jira attachment from unexpected host: %s", url
            )
            return None
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            resp = await client.get(
                url, auth=(settings.jira_email, settings.jira_api_token)
            )
        if resp.status_code != 200:
            logger.warning(
                "Jira attachment download HTTP %d for %s", resp.status_code, url
            )
            return None
        if len(resp.content) > settings.jira_max_image_bytes:
            logger.info(
                "Jira attachment exceeded size cap after download (%d bytes) — discarding",
                len(resp.content),
            )
            return None
        return resp.content
    except Exception:
        logger.exception("Jira attachment download failed for %s", url)
        return None


async def _fetch_jira_images(fields: dict) -> list[dict]:
    """Download every image attachment found by _extract_image_attachments
    concurrently. Only attempted when an Anthropic API key is configured —
    the only backend with vision support (see agents/jira_vision.py) — so a
    cli/cursor-only deployment never pays the download cost for images it
    can't use. Never raises; a per-image download failure just drops that
    image rather than failing the whole ticket fetch.
    """
    if not settings.anthropic_api_key:
        return []
    attachments = _extract_image_attachments(fields)
    if not attachments:
        return []
    contents = await asyncio.gather(
        *[_download_jira_attachment(att["url"]) for att in attachments]
    )
    images: list[dict] = []
    for att, content in zip(attachments, contents):
        if content is None:
            continue
        images.append(
            {"filename": att["filename"], "mime": att["mime"], "data": content}
        )
    return images


async def _fetch_jira(path: str) -> dict:
    """Fetch Jira issue via REST API, falling back to HTML scraping."""
    try:
        key = path.rstrip("/").split("/")[-1]

        if settings.jira_api_token and settings.jira_email:
            api_url = f"{settings.jira_base_url.rstrip('/')}/rest/api/3/issue/{key}"
            resp = None
            for attempt in range(_RETRY_MAX + 1):
                try:
                    async with httpx.AsyncClient(timeout=15) as client:
                        resp = await client.get(
                            api_url,
                            auth=(settings.jira_email, settings.jira_api_token),
                        )
                except httpx.TransportError:
                    logger.warning(
                        "Jira REST transport error (attempt %d/%d)",
                        attempt + 1,
                        _RETRY_MAX + 1,
                    )
                    if attempt < _RETRY_MAX:
                        await asyncio.sleep(_RETRY_DELAYS[attempt])
                        continue
                    return {
                        "error": "Could not reach the Jira server — please try again.",
                        "content": None,
                    }
                if resp.status_code >= 500 and attempt < _RETRY_MAX:
                    logger.warning(
                        "Jira REST HTTP %d (attempt %d/%d) — retrying",
                        resp.status_code,
                        attempt + 1,
                        _RETRY_MAX + 1,
                    )
                    await asyncio.sleep(_RETRY_DELAYS[attempt])
                    continue
                break
            if resp is not None and resp.status_code == 200:
                data = resp.json()
                fields = data.get("fields", {})
                description = fields.get("description", "") or ""
                if isinstance(description, dict):
                    description = _extract_adf_text(description)

                priority = _extract_priority(fields)
                labels = _extract_names(fields.get("labels"))
                components = _extract_names(fields.get("components"))
                comments = await _fetch_jira_comments(key)
                images = await _fetch_jira_images(fields)

                meta_lines = []
                if priority:
                    meta_lines.append(f"Priority: {priority}")
                if labels:
                    meta_lines.append(f"Labels: {', '.join(labels)}")
                if components:
                    meta_lines.append(f"Components: {', '.join(components)}")
                meta_block = ("\n".join(meta_lines) + "\n") if meta_lines else ""

                raw_text = (
                    f"{fields.get('summary', key)}\n{meta_block}{description}".strip()
                )
                if comments:
                    raw_text += "\n\n## Comments\n" + "\n".join(
                        f"- {c}" for c in comments
                    )

                ac_raw = fields.get(settings.jira_ac_field, "") or ""
                if isinstance(ac_raw, dict):
                    ac_raw = _extract_adf_text(ac_raw)
                acceptance_criteria = ac_raw.strip() or _extract_ac_from_description(
                    description
                )
                return {
                    "title": fields.get("summary", key),
                    "description": description,
                    "acceptance_criteria": acceptance_criteria,
                    "priority": priority,
                    "labels": labels,
                    "components": components,
                    "comments": comments,
                    "images": images,
                    "raw_text": raw_text,
                    "content": raw_text,
                }

            # Credentials were provided but the issue could not be read. Surface the
            # real reason instead of silently falling back to an (empty) HTML scrape
            # of the browse page — which would otherwise yield fabricated test cases.
            if resp is not None and resp.status_code in (401, 403, 404):
                return {
                    "error": (
                        f"Could not read Jira issue '{key}' (HTTP {resp.status_code}): "
                        "it does not exist, or the configured Jira account lacks "
                        "permission to view this project. "
                        "Verify the issue key and the API token's access, or paste the "
                        "ticket text and I'll generate test cases from it."
                    ),
                    "content": None,
                }

        jira_url = f"{settings.jira_base_url.rstrip('/')}/browse/{key}"
        return await _fetch_generic(jira_url)

    except Exception as exc:
        return {"error": str(exc), "content": None}


_MYSELF_TIMEOUT = 10  # bounded live-probe timeout (seconds)
_MAX_ACCOUNT_CHARS = 80  # cap on the externally-sourced displayName in markdown


def _sanitize_account(value: object) -> str:
    """Strip control chars and markdown-significant characters, and cap
    length, on an externally-sourced Jira displayName / email before it is
    embedded in returned markdown. Never raises."""
    try:
        text = str(value or "")
    except Exception:
        return ""
    cleaned = "".join(
        ch for ch in text if ch == " " or (0x20 < ord(ch) < 0x7F) or ord(ch) > 0x9F
    )
    cleaned = "".join(ch for ch in cleaned if ch not in "`*_[]<>|#\\")
    cleaned = " ".join(cleaned.split()).strip()
    if len(cleaned) > _MAX_ACCOUNT_CHARS:
        cleaned = cleaned[:_MAX_ACCOUNT_CHARS].rstrip() + "…"
    return cleaned


async def verify_jira_access(
    *,
    base_url: str = "",
    email: str = "",
    api_token: str = "",
) -> dict:
    """Live pre-flight probe of Jira credentials via GET /rest/api/3/myself.

    Never raises (this module's contract). Accepts explicit override
    credentials so freshly-entered values can be tested without relying on a
    possibly-frozen settings object; each empty argument falls back to the
    configured setting. Returns ``{"ok": bool, "error": str, "account": str}``.
    Distinguishes 401/403 bad-credentials from network/DNS failures in the
    error text. The returned account (displayName/email) is externally sourced
    and is sanitized before it is handed back. SSRF posture matches _fetch_jira
    (same operator-configured host, httpx with basic auth) plus an explicit
    http(s) scheme guard.
    """
    base_url = (base_url or settings.jira_base_url or "").strip().rstrip("/")
    email = (email or settings.jira_email or "").strip()
    api_token = (api_token or settings.jira_api_token or "").strip()
    if not (base_url and email and api_token):
        return {"ok": False, "error": "missing_credentials", "account": ""}
    if not base_url.startswith(("http://", "https://")):
        base_url = "https://" + base_url
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return {
            "ok": False,
            "error": "The Jira base URL is not a valid http(s) address.",
            "account": "",
        }
    api_url = f"{base_url}/rest/api/3/myself"
    try:
        async with httpx.AsyncClient(timeout=_MYSELF_TIMEOUT) as client:
            resp = await client.get(api_url, auth=(email, api_token))
    except httpx.TransportError as exc:
        logger.warning("verify_jira_access transport error: %s", type(exc).__name__)
        return {
            "ok": False,
            "error": (
                "Could not reach the Jira server (network or DNS error) — "
                "check the base URL and your connection."
            ),
            "account": "",
        }
    except Exception:
        logger.exception("verify_jira_access unexpected failure")
        return {
            "ok": False,
            "error": "Could not verify Jira access due to an unexpected error.",
            "account": "",
        }
    if resp.status_code == 200:
        account = ""
        try:
            data = resp.json()
            account = _sanitize_account(
                data.get("displayName") or data.get("emailAddress") or ""
            )
        except Exception:
            account = ""
        return {"ok": True, "error": "", "account": account}
    if resp.status_code in (401, 403):
        return {
            "ok": False,
            "error": (
                f"Jira rejected the credentials (HTTP {resp.status_code}) — the "
                "email or API token is wrong, or the account lacks access."
            ),
            "account": "",
        }
    return {
        "ok": False,
        "error": (
            f"Jira access check failed (HTTP {resp.status_code}). Verify the "
            "base URL and try again."
        ),
        "account": "",
    }


_AC_HEADING_RE = re.compile(r"(?im)^[\s>#*_-]*acceptance\s+criteria\s*:?\s*$")

# A line only counts as the NEXT section heading when it carries a real heading
# signal — otherwise an ordinary short AC line ("Account is locked") would be
# mistaken for a heading and cut the block short (NB-021). We require one of:
#   - a trailing colon        ("Notes:")
#   - a leading markdown hash  ("## Notes", "# Details")
#   - an ALL-CAPS label       ("NOTES", "OUT OF SCOPE")
_NEXT_HEADING_RE = re.compile(
    r"""(?mx)
    ^[\s>*_-]*(?:
        \#{1,6}\s*\S.{0,40}$          # markdown heading: leading #(s)
      | [A-Za-z][\w /-]{1,40}:\s*$    # label line ending in a colon
      | [A-Z0-9][A-Z0-9 /_-]{1,40}$   # ALL-CAPS label (no lowercase letters)
    )
    """
)


def _extract_ac_from_description(description: str) -> str:
    """Fallback AC extraction: scan a ticket description for an 'Acceptance
    Criteria' heading and return the block beneath it (QW-11 / I-023).

    Returns the text from just after the heading up to the next heading-like
    line (or end of text). Empty string when no such heading is present. Never
    raises.
    """
    try:
        if not description:
            return ""
        m = _AC_HEADING_RE.search(description)
        if not m:
            return ""
        rest = description[m.end() :].lstrip("\n")
        lines = rest.splitlines()
        collected: list[str] = []
        for line in lines:
            # Stop at the next section heading (a short label line), but only
            # after we've collected at least one content line.
            if (
                collected
                and _NEXT_HEADING_RE.match(line)
                and not re.match(r"^\s*[-*•\d]", line)
            ):
                break
            collected.append(line)
        return "\n".join(collected).strip()
    except Exception:
        logger.exception("_extract_ac_from_description failed — returning empty")
        return ""


_MAX_ADF_DEPTH = 200


def _extract_adf_text(node: dict, depth: int = 0) -> str:
    """Recursively extract plain text from Atlassian Document Format.

    Guards against pathologically deep / self-referential ADF blobs by capping
    recursion at _MAX_ADF_DEPTH — beyond that (or when `content` isn't a list)
    it short-circuits to "" rather than blowing the Python recursion limit.
    """
    if depth > _MAX_ADF_DEPTH:
        return ""
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return node.get("text", "")
    content = node.get("content", [])
    if not isinstance(content, list):
        return ""
    parts = [_extract_adf_text(child, depth + 1) for child in content]
    sep = (
        "\n"
        if node.get("type")
        in ("paragraph", "tableRow", "tableCell", "bulletList", "listItem")
        else " "
    )
    return sep.join(p for p in parts if p.strip())


_LOGIN_PATHS = ("/login", "/signin", "/auth", "id.atlassian.com", "accounts.google.com")

_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


class _SSRFBlocked(Exception):
    """Raised internally when a URL (initial or post-redirect) fails the
    scheme/DNS/public-IP guard. Always caught inside _fetch_generic and
    converted to the standard {"error": ..., "content": None} contract --
    never propagates to callers."""


async def _resolve_public_ip(hostname: str) -> tuple[str | None, str | None]:
    """Resolve hostname and return (first_public_ip, error); error is None on success.

    Every resolved address must be global — a hostname with even one
    private/reserved address among its answers is rejected, matching the
    original (pre-hardening) guard's behaviour. Never raises.
    """
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(hostname, None)
    except socket.gaierror:
        return None, f"Blocked: could not resolve hostname '{hostname}'"

    first_public: str | None = None
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if not addr.is_global:
            return None, "Blocked: non-public address"
        if first_public is None:
            first_public = info[4][0]
    if first_public is None:
        return None, "Blocked: non-public address"
    return first_public, None


async def _validate_public_url(url: str) -> tuple[str | None, str | None, str | None]:
    """Validate scheme + resolve a pinned public IP for `url`.

    Returns (hostname, resolved_ip, error) — error is None on success. Shared
    by the top-level entry guard (fetch_url_content) and by every hop of
    _fetch_generic's manual redirect loop, so a redirect target gets exactly
    the same scheme/DNS/public-IP check as the original URL. Never raises.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None, None, f"Blocked: scheme '{parsed.scheme}' not allowed"
    hostname = parsed.hostname
    if not hostname:
        return None, None, "Blocked: invalid hostname"
    resolved_ip, error = await _resolve_public_ip(hostname)
    if error:
        return None, None, error
    return hostname, resolved_ip, None


class PinnedIPTransport(httpx.AsyncHTTPTransport):
    """Force a single request's connection onto a pre-validated IP.

    _validate_public_url resolves and validates `hostname` once; this
    transport then ensures the actual TCP connection goes to that exact
    address, so a second DNS lookup performed later by the HTTP stack (e.g. a
    DNS-rebinding attacker answering differently the second time) cannot
    silently redirect the connection to a private address after the check
    passed. The Host header and TLS SNI are kept as the original hostname (via
    the `sni_hostname` request extension) so virtual-hosted/SNI-routed servers
    and certificate hostname verification are unaffected.
    """

    def __init__(self, hostname: str, pinned_ip: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._hostname = hostname
        self._pinned_ip = pinned_ip

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == self._hostname:
            request.url = request.url.copy_with(host=self._pinned_ip)
            request.headers.setdefault("host", self._hostname)
            request.extensions["sni_hostname"] = self._hostname
        return await super().handle_async_request(request)


@dataclass
class _HopResult:
    """A small *detached* record of one HTTP hop.

    _fetch_one_hop reads everything it needs (status, headers, body text)
    while the httpx client/connection is still open, then returns this — so no
    live httpx.Response (and no half-open socket) ever escapes the client's
    `async with` block. Fixes the per-redirect socket/fd leak (NB-001).
    """

    status_code: int
    headers: dict
    text: str


async def _fetch_one_hop(url: str) -> _HopResult:
    """Validate + IP-pin a single request, then return a detached _HopResult.

    The httpx client is opened and fully closed inside this function; the
    response body is read (`resp.text`) BEFORE the `async with` exits, so the
    connection is released and nothing live leaks out. Raises _SSRFBlocked on
    validation failure, or propagates httpx.TransportError (handled by the
    caller's retry loop).
    """
    hostname, pinned_ip, error = await _validate_public_url(url)
    if error:
        raise _SSRFBlocked(error)
    transport = PinnedIPTransport(hostname, pinned_ip)
    async with httpx.AsyncClient(
        timeout=15, transport=transport, follow_redirects=False
    ) as client:
        resp = await client.get(
            url, headers={"User-Agent": "Mozilla/5.0 QA-Agents/1.0"}
        )
        # Read the body now, while the connection is still open, so the socket
        # is consumed/released before the client closes — never returned live.
        return _HopResult(
            status_code=resp.status_code,
            headers=dict(resp.headers),
            text=resp.text,
        )


async def _follow_redirects_with_pinning(start_url: str) -> tuple[_HopResult, str]:
    """GET start_url, manually following up to _MAX_REDIRECTS redirects.

    Every hop — including the first — is independently validated and
    IP-pinned via _fetch_one_hop, so an attacker cannot use a public URL that
    redirects to a private/internal address (e.g. the cloud metadata IP
    169.254.169.254) to reach it. Returns (hop, final_url) where `hop` is the
    detached _HopResult of the final hop and final_url is the real (un-pinned)
    URL that produced it, for callers that inspect the true final host (e.g.
    the login-page heuristic below). Intermediate hops are fully consumed and
    closed inside _fetch_one_hop, so no socket leaks per redirect (NB-001).
    Raises _SSRFBlocked if any hop is blocked or the chain exceeds
    _MAX_REDIRECTS; propagates httpx.TransportError for connection failures.
    """
    current_url = start_url
    for _ in range(_MAX_REDIRECTS + 1):
        hop = await _fetch_one_hop(current_url)
        if hop.status_code in _REDIRECT_STATUSES and "location" in hop.headers:
            current_url = urljoin(current_url, hop.headers["location"])
            continue
        return hop, current_url
    raise _SSRFBlocked(f"Blocked: exceeded {_MAX_REDIRECTS} redirects")


async def _fetch_generic(url: str) -> dict:
    """Fetch any URL and extract text via BeautifulSoup. Never raises.

    SSRF-hardened: redirects are followed manually (max _MAX_REDIRECTS hops)
    instead of via httpx's follow_redirects, re-running the full
    scheme/DNS/public-IP guard on every hop, and every request is pinned to
    its validated IP so a second DNS lookup performed by the HTTP stack cannot
    retarget the connection after the check passed (DNS rebinding).
    """
    try:
        resp = None
        final_url = url
        for attempt in range(_RETRY_MAX + 1):
            try:
                resp, final_url = await _follow_redirects_with_pinning(url)
            except _SSRFBlocked as blocked:
                logger.warning("Generic fetch blocked for %s: %s", url, blocked.args[0])
                return {"error": blocked.args[0], "content": None}
            except httpx.TransportError as exc:
                logger.warning(
                    "Generic fetch transport error (attempt %d/%d) for %s: %s",
                    attempt + 1,
                    _RETRY_MAX + 1,
                    url,
                    exc,
                )
                if attempt < _RETRY_MAX:
                    await asyncio.sleep(_RETRY_DELAYS[attempt])
                    continue
                return {
                    "error": "Could not reach the server after several attempts — please try again.",
                    "content": None,
                }
            if resp.status_code >= 500 and attempt < _RETRY_MAX:
                logger.warning(
                    "Generic fetch HTTP %d (attempt %d/%d) for %s — retrying",
                    resp.status_code,
                    attempt + 1,
                    _RETRY_MAX + 1,
                    url,
                )
                await asyncio.sleep(_RETRY_DELAYS[attempt])
                continue
            break

        if resp is None:
            return {"error": "No response after retries", "content": None}

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

        soup = BeautifulSoup(resp.text, "lxml")
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

        # A JS-only SPA shell (e.g. a React/Vue app like SauceDemo) may serve a
        # short "enable JavaScript" message that is OVER _MIN_READABLE_CHARS, so
        # detect it independently of the readable-text gate and flag spa_shell so
        # tools/ui_extractor.py can escalate to a Tier-2 browser render.
        if _looks_like_js_rendered(resp.text, body_text):
            logger.info(
                "Detected JS-rendered SPA shell for %s — flagging spa_shell for "
                "browser-render escalation",
                url,
            )
            return {
                "title": title_text,
                "description": "",
                "acceptance_criteria": "",
                "raw_text": body_text[:5000],
                "raw_html": resp.text[:_MAX_RAW_HTML_CHARS],
                "content": "",
                "spa_shell": True,
                "error": None,
            }

        # Otherwise a 200 with no readable text means an auth wall or a genuinely
        # broken/empty page — refuse rather than return empty content the
        # generator would fabricate cases from.
        if len(body_text.strip()) < _MIN_READABLE_CHARS:
            return {
                "error": (
                    "Could not extract readable content from the page — it likely "
                    "requires authentication or is rendered with JavaScript (e.g. a "
                    "Jira or other single-page app). Please paste the ticket text and "
                    "I'll generate test cases from it."
                ),
                "content": None,
            }

        return {
            "title": title_text,
            "description": body_text[:3000],
            "acceptance_criteria": "",
            "raw_text": body_text[:5000],
            "raw_html": resp.text[:_MAX_RAW_HTML_CHARS],
            "content": body_text[:5000],
            "spa_shell": False,
            "error": None,
        }
    except Exception as exc:
        return {"error": str(exc), "content": None}
