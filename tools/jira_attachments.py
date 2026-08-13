"""Opt-in, credentialed retrieval of Jira attachment BYTES.

WHY THIS MODULE EXISTS
----------------------
Jira is read through the CALLING AGENT's own Atlassian MCP connection
(``tools/jira_mcp.py``), which returns attachment **metadata** and never the
image bytes -- and ``jira_mcp.py`` makes no outbound HTTP request of its own by
hard rule. So a ticket whose requirements live in mockups produced a suite
written from the ticket TEXT alone, and the only remedy was asking the tester to
re-attach screenshots that were already in Jira.

``QA_JIRA_ATTACHMENT_FETCH_ENABLED`` (default **OFF**) deliberately
re-introduces the credentialed REST path that the 2026-08-01 migration removed
-- but ONLY here, ONLY for ``/rest/api/3/attachment/content/{id}``, and ONLY
when an operator opts in AND supplies ``JIRA_BASE_URL`` + ``JIRA_EMAIL`` +
``JIRA_API_TOKEN``. Nothing about the hosted-MCP text path changes: this module
is never consulted for ticket TEXT, and with the flag off it makes no network
call and no behavioural difference at all.

CONTRACT (Hard Rule, see CLAUDE.md)
-----------------------------------
* Nothing here EVER raises to a caller. Every public function returns a value
  (``{"error": ..., "content": [...]}``), exactly like ``jira_fetcher`` /
  ``jira_mcp``.
* NO LLM call, of any kind, on any path. This module imports no ``llm``.
* The SSRF stack is reused verbatim from ``tools/jira_fetcher``: every hop --
  including a redirect to the Atlassian media host -- goes through
  ``_validate_public_url`` (scheme + DNS + public-IP gate) and is pinned to the
  validated IP with ``PinnedIPTransport``, so DNS rebinding between check and
  connect cannot retarget the connection.
* Basic-Auth credentials are sent over **HTTPS to the configured JIRA_BASE_URL
  host, and nowhere else**. Both halves of that are enforced per hop
  (:func:`_may_authenticate`): ``_validate_public_url`` permits ``http`` for the
  generic web fetcher, so a ``Location:`` header pointing at
  ``http://<same-jira-host>/`` would otherwise have replayed the API token in
  CLEARTEXT. It is likewise never replayed onto the media host, which carries
  its own signed token.
* Everything downloaded is size-capped BEFORE and DURING the read, and gated to
  an image MIME allowlist. A non-image, an oversize body, a 401 and an
  unreachable host are all ordinary failures that NAME themselves to the caller.
* Untrusted strings that reach a tester-facing message (the response's
  ``Content-Type``) are charset-filtered and length-capped first
  (:func:`_safe_mime`); signed media URLs are never logged whole
  (:func:`_log_target` keeps host + path and drops the query).
* The returned bytes are handed to the tester's OWN multimodal model as MCP
  image content on the existing ``IMAGE_JOB`` path. They are never persisted in
  the prep store and never sent to a server-side model.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx

from config.settings import settings
from tools.jira_fetcher import PinnedIPTransport, _validate_public_url

logger = logging.getLogger(__name__)

# Only real raster images are accepted. An SVG is deliberately NOT here: it is
# an XML document with script/entity semantics, not a picture.
_IMAGE_MIME_WHITELIST = frozenset(
    {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp", "image/bmp"}
)

# Attachment ids come from an UNTRUSTED host-submitted payload and are
# interpolated into a URL PATH, so they are syntax-gated before use: no "/",
# no "..", no query string, no scheme.
_ATTACHMENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")

# A Content-Type is attacker-influenceable text that ends up in a tester-facing
# failure message, so only these characters survive, capped.
_MIME_SAFE_RE = re.compile(r"[^A-Za-z0-9/+.\-]")
_MAX_MIME_CHARS = 60

# `redirect=false` asks Jira NOT to 303 straight at the media host. Both shapes
# are handled anyway (see _fetch_bytes_once): different Jira Cloud versions and
# proxies answer with the bytes, with a 3xx, or with a small JSON envelope
# carrying a temporary URL.
_REST_PATH = "/rest/api/3/attachment/content/{attachment_id}?redirect=false"

_TIMEOUT_S = 20
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)

# At most ONE indirection (Jira -> media host). More than that is not a shape
# Jira produces, so it is treated as a failure rather than followed.
_MAX_INDIRECTIONS = 1

# A JSON indirection envelope is tiny by nature; anything bigger is not one.
_MAX_JSON_BYTES = 8192


# --------------------------------------------------------------------------- #
# Settings accessors (all lenient, all never-raising)                          #
# --------------------------------------------------------------------------- #


def enabled() -> bool:
    """Always False. Never raises.

    QA_JIRA_ATTACHMENT_FETCH_ENABLED was DELETED on 2026-08-13 (flag-surface
    reduction, batch 6) and the credentialed attachment fetch hardcoded OFF, so
    NO module in this tree makes an outbound request carrying JIRA_EMAIL /
    JIRA_API_TOKEN. The rest of this module is kept, unreachable, because its
    SSRF stack, MIME allowlist and per-hop credential re-check are the contract
    a revival must satisfy -- see CLAUDE.md and docs/FEATURE_FLAGS.md.
    """
    return False


def _base_url() -> str:
    """Configured Jira base URL with any trailing slash removed. Never raises."""
    try:
        return str(getattr(settings, "jira_base_url", "") or "").strip().rstrip("/")
    except Exception:  # pragma: no cover - settings is lenient by contract
        return ""


def _credentials() -> tuple[str, str]:
    """(email, api_token) from settings. Never raises, never logged."""
    try:
        return (
            str(getattr(settings, "jira_email", "") or "").strip(),
            str(getattr(settings, "jira_api_token", "") or "").strip(),
        )
    except Exception:  # pragma: no cover - settings is lenient by contract
        return "", ""


def _per_image_cap() -> int:
    """Per-attachment byte cap (JIRA_MAX_IMAGE_BYTES). Never raises."""
    try:
        return max(1, int(getattr(settings, "jira_max_image_bytes", 5_000_000) or 0))
    except Exception:  # pragma: no cover - settings is lenient by contract
        return 5_000_000


# The SAME window mcp_handlers._jira_image_cap applies (2026-08-09, review L4).
# That module must NOT be imported here, so the two literals are duplicated and
# tests/test_genquality_batch_c.py pins them equal. Unclamped, JIRA_MAX_IMAGES=25
# let a fetch of 22 satisfy the gate's own min(25, 20) = 20 and the completeness
# gate went silent on three missing screens. lo=1 because a cap of zero makes
# "complete" vacuous -- the way to fetch nothing is
# QA_JIRA_ATTACHMENT_FETCH_ENABLED=false, and _total_cap already did max(1, ...).
_MAX_IMAGES_LO = 1
_MAX_IMAGES_HI = 20


def _max_images() -> int:
    """How many attachments may be downloaded (JIRA_MAX_IMAGES). Never raises.

    Clamped to [_MAX_IMAGES_LO, _MAX_IMAGES_HI], the same window the
    completeness gate uses, so the downloader and the gate can never disagree
    about how many screens this server will ever carry. The coercion mirrors
    _clamped_count EXACTLY (review C2): a bare int() whose failure defaults to 3,
    and NO ``or 0`` -- that idiom turned a None or 0 setting into 1 here while
    the gate read 3, which is precisely the drift this pairing exists to
    prevent."""
    try:
        n = int(getattr(settings, "jira_max_images", 3))
    except Exception:  # pragma: no cover - settings is lenient by contract
        n = 3
    return max(_MAX_IMAGES_LO, min(_MAX_IMAGES_HI, n))


def _total_cap() -> int:
    """Whole-ticket byte budget for one fetch.

    The per-image cap times the image count, clamped by QA_PREP_MAX_BYTES so the
    downloaded bytes can never exceed what the prepare path is willing to carry.
    Never raises.
    """
    try:
        prep_cap = max(0, int(getattr(settings, "qa_prep_max_bytes", 4_000_000) or 0))
    except Exception:  # pragma: no cover - settings is lenient by contract
        prep_cap = 4_000_000
    budget = _per_image_cap() * max(1, _max_images())
    return min(budget, prep_cap) if prep_cap else budget


# --------------------------------------------------------------------------- #
# URL / string helpers (pure)                                                  #
# --------------------------------------------------------------------------- #


def _host_of(url: str) -> str:
    """Lowercased hostname of *url*, or "". Never raises."""
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _scheme_of(url: str) -> str:
    """Lowercased scheme of *url*, or "". Never raises."""
    try:
        return (urlparse(url).scheme or "").lower()
    except Exception:
        return ""


def _log_target(url: str) -> str:
    """``host/path`` for logging -- the QUERY STRING IS DROPPED.

    The second hop's URL is a SIGNED media link (``?token=...``); logging it
    whole would write a working credential into the install's log file. Never
    raises.
    """
    try:
        parsed = urlparse(url)
        return f"{(parsed.hostname or '?').lower()}{parsed.path or ''}"[:200]
    except Exception:
        return "?"


def _safe_mime(value: object) -> str:
    """Charset-filtered, length-capped MIME token safe to echo to a tester.

    A ``Content-Type`` is attacker-influenceable and ends up in
    ``url_content["image_fetch_error"]`` and from there in a markdown reply.
    Never raises.
    """
    try:
        return _MIME_SAFE_RE.sub("", str(value or ""))[:_MAX_MIME_CHARS]
    except Exception:
        return ""


def _may_authenticate(url: str) -> bool:
    """Whether the stored Basic-Auth credential may be sent to *url*.

    HTTPS **and** the configured Jira host, both required. ``_validate_public_url``
    deliberately permits ``http`` (the generic web fetcher needs it), so the
    scheme half is what stops a ``Location: http://<jira-host>/...`` redirect
    from replaying the API token in cleartext. Never raises.
    """
    if _scheme_of(url) != "https":
        return False
    host = _host_of(url)
    return bool(host) and host == _host_of(_base_url())


def attachment_url(att: object) -> str:
    """The credentialed REST content URL for one attachment record, or "".

    Prefers the syntax-gated attachment ``id`` (built against the operator's own
    ``JIRA_BASE_URL``, so a hostile payload cannot point the request anywhere
    else). Falls back to the payload's own ``content`` URL ONLY when it is on
    that same host. Never raises.
    """
    try:
        base = _base_url()
        if not base or not isinstance(att, dict):
            return ""
        raw_id = str(att.get("id") or "").strip()
        if raw_id and _ATTACHMENT_ID_RE.match(raw_id):
            return base + _REST_PATH.format(attachment_id=raw_id)
        content = str(att.get("content") or "").strip()
        if content and _host_of(content) and _host_of(content) == _host_of(base):
            return content
        return ""
    except Exception:
        logger.exception("attachment_url failed - skipping the attachment")
        return ""


def _ordered(attachments: list) -> list:
    """INLINE images first, ticket order otherwise.

    ``JIRA_MAX_IMAGES`` truncates, and an image pasted INTO the description or a
    comment is far likelier to be the mockup the requirements live in than a
    stray upload at the bottom of the ticket -- so the inline ones, which
    ``jira_mcp._match_media_to_attachments`` marks from the ADF media nodes, win
    the budget. ``sorted`` is stable, so everything else keeps ticket order.
    Never raises.
    """
    try:
        return sorted(attachments, key=lambda a: 0 if a.get("inline") else 1)
    except Exception:
        logger.debug("_ordered failed - keeping ticket order", exc_info=True)
        return list(attachments)


# --------------------------------------------------------------------------- #
# Response inspection (pure)                                                   #
# --------------------------------------------------------------------------- #


def _header(headers: object, name: str) -> str:
    """Case-insensitive header lookup over a plain dict. Never raises."""
    try:
        if not isinstance(headers, dict):
            return ""
        wanted = name.lower()
        for key, value in headers.items():
            if str(key).lower() == wanted:
                return str(value or "")
        return ""
    except Exception:
        return ""


def _response_mime(headers: object) -> str:
    """Bare content-type (no charset), lowercased and charset-filtered."""
    raw = _header(headers, "content-type").split(";", 1)[0].strip().lower()
    return _safe_mime(raw)


def check_response(status: int, headers: object, cap: int) -> tuple[str, str]:
    """``(mime, error)`` for a download response, BEFORE the body is read.

    A 401/403 names the credential as the cause -- that is the single most
    likely failure on a real install, because a Jira API token expires. An
    oversize declared Content-Length is refused without reading a byte. The mime
    echoed in the error text is already sanitized by :func:`_safe_mime`. Never
    raises.
    """
    try:
        if status in (401, 403):
            return "", (
                f"Jira rejected the stored credentials (HTTP {status}) -- "
                "JIRA_EMAIL / JIRA_API_TOKEN are missing, wrong or expired"
            )
        if status >= 400:
            return "", f"Jira returned HTTP {status} for the attachment"
        declared = 0
        raw_length = _header(headers, "content-length")
        if raw_length:
            try:
                declared = int(raw_length.strip())
            except (TypeError, ValueError):
                declared = 0
        if declared and declared > cap:
            return "", f"the attachment is larger than the {cap}-byte cap"
        mime = _response_mime(headers)
        if mime and mime not in _IMAGE_MIME_WHITELIST and mime != "application/json":
            return "", f"unsupported attachment type: {mime}"
        return mime, ""
    except Exception:
        logger.exception("check_response failed - refusing the attachment")
        return "", "the attachment response could not be inspected"


async def read_capped(chunks: object, cap: int) -> tuple[bytes, str]:
    """Read an async byte iterator, aborting the moment it exceeds *cap*.

    The declared Content-Length is a CLAIM; this is the enforcement. Returns
    ``(data, error)`` and never raises.
    """
    buffer = bytearray()
    try:
        async for chunk in chunks:  # type: ignore[union-attr]
            if not chunk:
                continue
            buffer.extend(chunk)
            if len(buffer) > cap:
                return b"", f"the attachment exceeded the {cap}-byte cap"
        return bytes(buffer), ""
    except Exception:
        logger.exception("read_capped failed - dropping the attachment")
        return b"", "reading the attachment failed"


def _url_from_json(data: bytes) -> str:
    """Temporary download URL out of a small JSON indirection envelope, or "".

    ``json.loads`` only (never eval), size-capped, and **https only** -- an
    ``http://`` target would downgrade the next hop, which is both a plaintext
    leak of a signed link and a redirect this module has no reason to accept.
    Never raises.
    """
    try:
        if not data or len(data) > _MAX_JSON_BYTES:
            return ""
        payload = json.loads(bytes(data).decode("utf-8", "ignore"))
        if not isinstance(payload, dict):
            return ""
        for key in ("url", "content", "href", "location"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip().lower().startswith("https://"):
                return value.strip()
        return ""
    except Exception:
        logger.debug("_url_from_json: not an indirection envelope", exc_info=True)
        return ""


# --------------------------------------------------------------------------- #
# HTTP (the only network in this module)                                       #
# --------------------------------------------------------------------------- #


async def _fetch_bytes_once(
    url: str, *, auth: tuple[str, str] | None, cap: int
) -> tuple[bytes, str, str, str]:
    """ONE validated, IP-pinned GET.

    Returns ``(data, mime, next_url, error)``. ``next_url`` is non-empty when
    the hop was an indirection (3xx Location, or a JSON envelope) that the
    caller must follow; a 3xx returns WITHOUT reading a body. Never raises:
    every failure is an ``error`` string.
    """
    try:
        hostname, pinned_ip, error = await _validate_public_url(url)
        if error:
            return b"", "", "", error
        transport = PinnedIPTransport(hostname, pinned_ip)
        headers = {
            "Accept": "image/*, application/json;q=0.5",
            "User-Agent": "Mozilla/5.0 QA-Agents/1.0",
        }
        async with httpx.AsyncClient(
            timeout=_TIMEOUT_S, transport=transport, follow_redirects=False
        ) as client:
            async with client.stream(
                "GET", url, headers=headers, auth=auth
            ) as response:
                response_headers = dict(response.headers)
                if response.status_code in _REDIRECT_STATUSES:
                    location = _header(response_headers, "location")
                    if not location:
                        return (
                            b"",
                            "",
                            "",
                            f"Jira returned HTTP {response.status_code} with no "
                            "redirect target",
                        )
                    return b"", "", urljoin(url, location), ""
                mime, check_error = check_response(
                    response.status_code, response_headers, cap
                )
                if check_error:
                    return b"", "", "", check_error
                data, read_error = await read_capped(response.aiter_bytes(), cap)
                if read_error:
                    return b"", "", "", read_error
                if mime == "application/json":
                    next_url = _url_from_json(data)
                    if not next_url:
                        return b"", mime, "", "the attachment response was not an image"
                    return b"", mime, next_url, ""
                return bytes(data), mime, "", ""
    except Exception:
        # host + path ONLY: the media hop's URL carries a signed ?token=.
        logger.exception("Jira attachment hop failed for %s", _log_target(url))
        return b"", "", "", "the attachment request failed"


async def _download_one(att: dict, *, cap: int) -> tuple[dict | None, str]:
    """Download ONE attachment. Returns ``(image, error)``; never raises.

    ``image`` is the ``{filename, mime, data}`` shape the host-image path
    already carries (``mcp_handlers._select_prepare_images``).
    """
    try:
        url = attachment_url(att)
        if not url:
            return None, "no usable attachment id or content URL"
        email, token = _credentials()

        def _auth_for(target: str) -> tuple[str, str] | None:
            # HTTPS + the configured Jira host, both required, re-evaluated on
            # EVERY hop -- see _may_authenticate.
            if email and token and _may_authenticate(target):
                return (email, token)
            return None

        current = url
        for _ in range(_MAX_INDIRECTIONS + 1):
            data, mime, next_url, error = await _fetch_bytes_once(
                current, auth=_auth_for(current), cap=cap
            )
            if error:
                return None, error
            if next_url:
                current = next_url
                continue
            if not data:
                return None, "the attachment came back empty"
            if mime not in _IMAGE_MIME_WHITELIST:
                return None, f"unsupported attachment type: {_safe_mime(mime) or '?'}"
            return (
                {
                    "filename": str(att.get("filename") or "attachment"),
                    "mime": mime,
                    "data": data,
                },
                "",
            )
        return None, "too many attachment redirects"
    except Exception:
        logger.exception("_download_one failed - reporting the attachment as lost")
        return None, "the attachment could not be downloaded"


# --------------------------------------------------------------------------- #
# Public entry point                                                           #
# --------------------------------------------------------------------------- #


async def fetch_attachment_bytes(attachments: object) -> dict:
    """Download the image attachments of ONE ticket.

    Returns ``{"error": <str|None>, "content": [{filename, mime, data}],
    "failures": [{"filename": ..., "reason": ...}]}``. ``error`` is set only
    when NOTHING could be fetched, so a partial success is a success with named
    failures. Never raises, and makes no request at all when the flag is off or
    the credentials/base URL are missing.
    """
    result: dict = {"error": None, "content": [], "failures": []}
    try:
        if not enabled():
            result["error"] = (
                "server-side attachment fetching is off "
                "(QA_JIRA_ATTACHMENT_FETCH_ENABLED)"
            )
            return result
        if not _base_url():
            result["error"] = (
                "JIRA_BASE_URL is not configured, so there is no host to ask for "
                "the attachment bytes"
            )
            return result
        email, token = _credentials()
        if not (email and token):
            result["error"] = (
                "JIRA_EMAIL / JIRA_API_TOKEN are not configured, so the "
                "attachment endpoint cannot be authenticated"
            )
            return result
        items = [a for a in (attachments or []) if isinstance(a, dict)]
        if not items:
            return result
        per_cap = _per_image_cap()
        budget = _total_cap()
        used = 0
        for att in _ordered(items)[: _max_images()]:
            name = str(att.get("filename") or "attachment")
            remaining = budget - used
            if remaining <= 0:
                result["failures"].append(
                    {
                        "filename": name,
                        "reason": "the ticket image byte budget was already spent",
                    }
                )
                continue
            image, error = await _download_one(att, cap=min(per_cap, remaining))
            if error or not image:
                result["failures"].append(
                    {"filename": name, "reason": error or "unknown failure"}
                )
                continue
            used += len(image["data"])
            result["content"].append(image)
        if result["failures"] and not result["content"]:
            result["error"] = "; ".join(
                f"{f['filename']}: {f['reason']}" for f in result["failures"]
            )[:500]
        return result
    except Exception:
        logger.exception("fetch_attachment_bytes failed")
        return {
            "error": "fetching the ticket attachments failed",
            "content": [],
            "failures": [],
        }
