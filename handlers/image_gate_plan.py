"""Jira image gate: source-plan normalisation, the Jira-source test and the rejected-plan note."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from config.settings_guard import never_raise
from handlers import _elicit, jira
from handlers.image_gate_labels import (
    _IMAGE_PLAN_ALIASES,
    _IMAGE_PLAN_ORDINALS,
    _IMAGE_SOURCE_LABELS,
    _IMAGE_SOURCE_PLANS,
)
from tools import settings_port

logger = logging.getLogger("tools.mcp_handlers")


def _plan_from_label(raw: str) -> str:
    """Return the plan a menu label echoed verbatim maps to, or an empty string."""
    for label, mapped in _IMAGE_SOURCE_LABELS.items():
        if raw == label.strip().lower():
            return mapped
    return ""


def _strip_plan_prefix(raw: str) -> str:
    """Drop an "option N" style lead and the menu punctuation around the value."""
    for _lead in ("option ", "option_", "#"):
        if raw.startswith(_lead):
            raw = raw[len(_lead) :].strip()
    return raw.strip(".) ")


def _snake_plan(raw: str) -> str:
    """Collapse separators in a free-text plan to single underscores."""
    for ch in (" ", "-", "+", "/", ".", ":", "\t"):
        raw = raw.replace(ch, "_")
    while "__" in raw:
        raw = raw.replace("__", "_")
    return raw.strip("_")


# Coerce a host/tester-supplied ``source_plan`` to one of _IMAGE_SOURCE_PLANS, or
# "" when it is unusable -- which RE-ASKS rather than guessing which channel the
# tester meant. Never raises.
#
# 2026-08-31: accepts the three shapes a real caller sends that this used to
# refuse -- the five _IMAGE_SOURCE_LABELS verbatim (a host relaying the tier-3
# markdown may echo the LABEL the tester clicked, not the bracketed token), the
# menu ordinals 1-5, and an "option N" prefix. The labels are matched on the RAW
# lowercased string, before the separator mangling turns their "--" into
# underscores.
def _normalize_source_plan(plan: str) -> str:
    """Return the usable source plan a host or tester sent, or an empty string."""
    with never_raise(
        logger, "_normalize_source_plan failed for %r", plan, exc_info=True
    ):
        raw = (plan or "").strip().lower()
        if not raw:
            return ""
        mapped = _plan_from_label(raw)
        if mapped:
            return mapped
        raw = _strip_plan_prefix(raw)
        if raw in _IMAGE_PLAN_ORDINALS:
            return _IMAGE_PLAN_ORDINALS[raw]
        raw = _snake_plan(raw)
        raw = _IMAGE_PLAN_ALIASES.get(raw, raw)
        return raw if raw in _IMAGE_SOURCE_PLANS else ""
    return ""


def _gate_jira_source(url: str) -> bool:
    """True when *url* is a Jira source BEAT 1 should fire on.

    _looks_like_jira_host only matches atlassian.net / jira.* / .jira.*, so a
    self-hosted Jira on a plain corporate domain (tickets.acme.com) would never
    see the disclosure even though it applies verbatim -- so the configured
    JIRA_BASE_URL host counts too. Never raises."""
    with never_raise(logger, "_gate_jira_source failed for %r", url, exc_info=True):
        if jira._looks_like_jira_host(url):
            return True
        base = (getattr(settings_port.get(), "jira_base_url", "") or "").strip()
        if not base:
            return False
        base_host = (urlparse(base).hostname or "").lower()
        host = (urlparse(url).hostname or "").lower()
        return bool(base_host and host and host == base_host)
    return False


# One line naming a `source_plan` this server could not recognise, or ""
# when there is nothing to disclose. Host-supplied text, so it is stripped of
# line breaks and backticks and hard-capped before it is echoed. Never
# raises.
def _rejected_plan_note(raw: str) -> str:
    """Return one line naming a source_plan this server could not recognise, or an empty string."""
    with never_raise(logger, "_rejected_plan_note failed for %r", raw, exc_info=True):
        val = (raw or "").strip()
        if not val or _normalize_source_plan(val):
            return ""
        for ch in ("\r", "\n", "`"):
            val = val.replace(ch, " ")
        val = " ".join(val.split())[:60]
        if not val:
            return ""
        return (
            "> \u26a0\ufe0f  **I did not recognise `source_plan='" + val + "'`, so "
            "it was ignored** -- I am asking the question below instead of "
            "guessing which channel you meant. It must be EXACTLY one of "
            "`jira`, `jira_attach`, `jira_device`, `jira_both`, `device`.\n\n"
        )
    return ""


# True when THIS client's dialogs are switched off by the strike gate
# rather than absent from the client.
#
# Both states reach _image_gate_menu_markdown as "unavailable", and only
# one of them is a statement about the client's capabilities. Never raises
# -- a read failure means "not gated", which falls back to the wording that
# was already there.
def _gated_not_unsupported() -> bool:
    """Return True when this client's dialogs are off by the strike gate, not absent from the client."""
    with never_raise(logger, "gated-cause read failed", exc_info=True):
        return bool(_elicit.elicit_client_gated())
    return False
