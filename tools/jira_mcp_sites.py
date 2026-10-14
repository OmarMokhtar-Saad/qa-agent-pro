"""Jira MCP: sites helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from tools.jira_mcp_errors import _MAX_ECHO_CHARS, _sanitize_echo

logger = logging.getLogger("tools.jira_mcp")

# At most this many site NAMES are ever echoed back, each sanitized.
_MAX_ECHO_SITES = 5


# Same best-effort, STRING-only discipline as the identity keys below: an
# unfamiliar entry yields "" rather than a fabricated host.
_SITE_URL_KEYS = ("url", "siteUrl", "self", "baseUrl")


_SITE_NAME_KEYS = ("name", "displayName")


def _looks_like_site_url(value: str) -> bool:
    """True for a string that can be read as a site URL rather than guessed at.

    Either it carries a scheme, or it is a bare hostname: a dot, no whitespace,
    and no path-ish or ARI punctuation. "acme.atlassian.net" passes; "Acme Team
    Site", "ari:cloud:jira::site/1234-abcd" and a bare cloudId do not.
    """
    try:
        candidate = value.strip()
        if not candidate or any(ch.isspace() for ch in candidate):
            return False
        if "://" in candidate:
            return True
        if ":" in candidate or "/" in candidate:
            return False
        return "." in candidate and not candidate.startswith(".")
    except AttributeError:  # pragma: no cover - defensive
        logger.debug("_looks_like_site_url failed", exc_info=True)
        return False


# A plain list of site URLs is a legitimate shape, and reading only dicts turned
# it into "no readable sites" -- which used to be a hard negative. But only a
# URL-SHAPED string may be read as one: coercing every string produced a
# readable-looking host from a site NAME or a cloudId, and a host that misses
# is a hard negative persisted for a week. An unrecognised string is "" ->
# unreadable, which is the honest answer and costs the tester nothing.
def _site_entry(entry: object) -> object:
    """A string entry as a ``{"url": ...}`` dict when URL-shaped, else ``{}``."""
    if isinstance(entry, str):
        return {"url": entry} if _looks_like_site_url(entry) else {}
    return entry


def _url_host(value: object) -> str:
    """Lowercased hostname of one URL value (https assumed when no scheme), or ""."""
    if not isinstance(value, str) or not value.strip():
        return ""
    candidate = value.strip()
    if "://" not in candidate:
        candidate = "https://" + candidate
    try:
        return (urlparse(candidate).hostname or "").lower()
    except ValueError:
        return ""


def site_host(entry: object) -> str:
    """Lowercased hostname of one accessible-resource entry, or "".

    STRING fields only; an unfamiliar shape yields "" rather than a guess, and a
    value with no scheme is probed as https so a bare "acme.atlassian.net" still
    resolves. Never raises.
    """
    try:
        entry = _site_entry(entry)
        if not isinstance(entry, dict):
            return ""
        for key in _SITE_URL_KEYS:
            host = _url_host(entry.get(key))
            if host:
                return host
        return ""
    except Exception:
        logger.exception("site_host failed - dropping the entry")
        return ""


# Never renders the site URL and never renders the cloudId: a UUID is an
# identifier the tester did not ask to see, and the URL is the untrusted half
# of the payload. ``_sanitize_echo`` strips URLs, so a NAME that is itself a
# URL degrades to "" and the caller says "an unnamed site".
def _site_label(entry: object) -> str:
    """Sanitized, URL-free display name for one site entry."""
    try:
        if not isinstance(entry, dict):
            return ""
        for key in _SITE_NAME_KEYS:
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                cleaned = _sanitize_echo(value, _MAX_ECHO_CHARS)
                if cleaned:
                    return cleaned
        return ""
    except Exception:
        logger.exception("_site_label failed - omitting the name")
        return ""


def _render_sites(sites: object) -> str:
    """At most ``_MAX_ECHO_SITES`` sanitized site NAMES plus a count. Never a
    URL, never a cloudId."""
    try:
        if not isinstance(sites, list):
            return ""
        names = [
            _site_label(entry) or "an unnamed site" for entry in sites[:_MAX_ECHO_SITES]
        ]
        rendered = ", ".join(n for n in names if n)
        extra = len(sites) - len(names)
        if extra > 0:
            rendered += f", and {extra} more"
        return rendered
    except Exception:
        logger.exception("_render_sites failed - omitting the list")
        return ""


def _site_hosts(sites: list) -> tuple[list, list]:
    """(all hosts read from *sites*, the subset that is EXPLICIT).

    A host is EXPLICIT when the client structured it -- a dict entry, or a
    string carrying a scheme. A bare dotted hostname is our inference, and
    an inference may confirm a match but must never establish a miss.
    """
    hosts = []
    explicit = []
    for entry in sites:
        host = site_host(entry)
        if not host:
            continue
        hosts.append(host)
        if isinstance(entry, dict) or (isinstance(entry, str) and "://" in entry):
            explicit.append(host)
    return hosts, explicit
