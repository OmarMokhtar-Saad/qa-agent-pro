"""Jira MCP: access helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import logging
from typing import cast
from urllib.parse import urlparse

from tools import settings_port
from tools.jira_mcp_errors import _carries_error_key, _error_text
from tools.jira_mcp_identity import _unwrap_mcp_content
from tools.jira_mcp_sites import _site_hosts

logger = logging.getLogger("tools.jira_mcp")

# Envelope keys this server ASKS the agent for. A bare identity object -- the
# pre-2026-09-01 shape, and what a client with a cached tool list keeps sending
# -- is still accepted, and reported as identity-only rather than as access.
_ENVELOPE_USER_KEYS = ("user", "userInfo", "identity")


_ENVELOPE_SITES_KEYS = ("resources", "sites", "accessibleResources")


def configured_jira_host() -> str:
    """Hostname of the configured ``JIRA_BASE_URL``, or "".

    getattr with a default, so a settings object without the field cannot raise.
    """
    try:
        base = str(getattr(settings_port.get(), "jira_base_url", "") or "").strip()
        if not base:
            return ""
        candidate = base if "://" in base else "https://" + base
        return (urlparse(candidate).hostname or "").lower()
    except Exception:
        logger.exception("configured_jira_host failed")
        return ""


def _envelope_sites(payload: dict) -> tuple[object, bool]:
    """The sites half of the envelope and whether one was seen."""
    for key in _ENVELOPE_SITES_KEYS:
        if key in payload:
            return payload.get(key), True
    return None, False


def _envelope_identity(payload: dict, seen: bool) -> tuple[object, bool]:
    """The identity half of the envelope and the updated ``seen`` flag."""
    for key in _ENVELOPE_USER_KEYS:
        value = payload.get(key)
        if isinstance(value, dict):
            return _unwrap_mcp_content(value), True
    if seen:
        # Sites present, no user key: keep everything EXCEPT the sites
        # half, so the identity check judges identity fields only.
        keep = {k: v for k, v in payload.items() if k not in _ENVELOPE_SITES_KEYS}
        return keep, seen
    return payload, seen


# Split the agent's blob into ``(identity, sites, envelope_seen)``.
#
# A bare identity object returns ``(payload, None, False)`` so every existing
# client keeps working. When an envelope IS seen, the identity half is
# isolated from the sites half -- otherwise ``_error_text`` and
# ``_identity_label`` would run against a dict that still carries the site
# list, and a string inside a site entry could be read as an identity.
def _split_envelope(payload: dict) -> tuple:
    """Split the agent's blob into ``(identity, sites, envelope_seen)``. Never raises."""
    try:
        if not isinstance(payload, dict):
            return ({}, None, False)
        sites, seen = _envelope_sites(payload)
        identity, seen = _envelope_identity(payload, seen)
        if isinstance(sites, dict):
            unwrapped = _unwrap_mcp_content(sites)
            sites = unwrapped.get("values", unwrapped)
        return (identity if isinstance(identity, dict) else {}, sites, seen)
    except Exception:
        logger.exception("_split_envelope failed - treating as identity only")
        return (payload if isinstance(payload, dict) else {}, None, False)


def _access_guard(sites: object) -> str:
    """The verdict for a sites half that is not a list, or "" when it is one."""
    if sites is None:
        return "not_supplied"
    # An error marker on the sites half is a REPORTED reason ("insufficient
    # scope"), not an unreadable shape. Flattening it to "unreadable" would
    # discard the one sentence that tells the tester what to fix.
    if isinstance(sites, dict) and (_error_text(sites) or _carries_error_key(sites)):
        return "sites_error"
    if not isinstance(sites, list):
        return "unreadable"
    return ""


# ("inferred" joins "unreadable" as a non-accusing outcome: both report the
# sign-in and decline to judge the site.) A list we could not read is NOT proof
# the account reaches nothing. Reporting it as such told a correctly-connected
# tester to sign in as someone else, with no way to disagree. Only a genuinely
# empty list is that answer. A miss is reported only if the list we compared
# against was the client's own -- otherwise it is a claim about strings we
# interpreted.
def _access_compare(sites: list) -> str:
    """The verdict for a readable list of sites."""
    hosts, explicit = _site_hosts(sites)
    if not hosts:
        return "empty" if not sites else "unreadable"
    configured = configured_jira_host()
    if not configured:
        return "no_host"
    if configured in hosts:
        return "match"
    return "no_match" if explicit else "inferred"


# ``no_host`` is a deliberate departure from the original design, which
# accepted any host that merely LOOKED like Jira when ``JIRA_BASE_URL`` was
# unset. Every ``*.atlassian.net`` satisfies that, including the personal
# account this probe exists to catch, so the fallback would have rubber-stamped
# the failure case. With nothing to compare against, "I could not check" is the
# only honest answer.
def access_verdict(sites: object) -> str:
    """One of ``not_supplied``, ``sites_error``, ``unreadable``, ``empty``,
    ``no_host``, ``no_match``, ``match``.

    The SINGLE parse of the access half: the rendered message and the persisted
    verdict both come from it, because two parsers of one payload is how a
    stored verdict drifts from the one the tester was shown. Never raises.
    """
    try:
        return _access_guard(sites) or _access_compare(cast("list", sites))
    except Exception:
        logger.exception("access_verdict failed - reporting unreadable")
        return "unreadable"
