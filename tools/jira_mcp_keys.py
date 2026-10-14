"""Jira MCP: keys helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, urlparse

from config.settings_guard import never_raise
from tools import settings_port

logger = logging.getLogger("tools.jira_mcp")

# Jira issue keys taken from a RESPONSE (parent.key, subtask keys, linked issue
# keys) are echoed back into tester-facing text and into the next directive.
# Treat them as untrusted: a strict syntax gate stops a crafted response from
# smuggling "../", an absolute URL, or a query string through.
#
# \Z (not $) so a trailing newline cannot slip past the anchor; the {0,63} bound
# keeps an absurdly long project prefix out of the directive entirely.
#
# 2026-09-02 audit F19: the grammar is ASCII-ONLY and explicit about every
# class, because this value is spliced into an export FILENAME
# (mcp_handlers._export_ticket_slug) and into tester-facing text. The previous
# pattern used `\d`, which in Python matches EVERY Unicode decimal -- so
# "TICKET-\u0661" was a "valid key", passed the slug helper's `ch.isalnum()`
# re-check (true for Arabic-Indic digits) and landed in a path. `_` went for
# the same reason: no Jira project key contains one, so accepting it only
# widened what could be echoed. The project part is ASCII letters/digits, the
# number part is [0-9] only.
#
# This encodes the Jira CLOUD key grammar, which is what this server reads
# (through the host's mcp.atlassian.com connection). A Jira DATA CENTER install
# whose `jira.projectkey.pattern` has been customised to allow an underscore
# would find such a key unreadable here -- deliberate, and named so it is
# recognised rather than re-derived.
_ISSUE_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,63}-[0-9]{1,10}\Z")


def _valid_issue_key(value: object) -> str:
    """Return *value* UPPERCASED when it is a syntactically valid Jira issue
    key, else "".

    Security gate for every key that comes back inside a host-submitted payload
    and is later echoed into text. Never raises.

    2026-09-02 audit F19: the return is now the canonical UPPERCASE form. Jira
    issue keys are uppercase by definition, so "TICKET-1" and "TICKET-1" name one
    ticket -- but they are two different strings, which is exactly what let a
    lowercase copy reach an export filename and would have defeated the key
    comparison in :func:`normalize_issue_payload`. Canonicalizing here gives
    every caller one spelling; a lowercase URL a tester pasted still resolves.
    """
    if not isinstance(value, str):
        return ""
    return value.upper() if _ISSUE_KEY_RE.match(value) else ""


# The issue key when *value* is a BARE key and nothing else, else "".
#
# ``qa_prepare_test_cases("TICKET-7154")`` used to be treated as a feature
# DESCRIPTION and generated a full suite about the literal string -- cases
# grounded in nothing (2026-09-02 audit F8). Callers use this to route such an
# input to the Atlassian fetch directive instead. The WHOLE input must be the
# key: a key cited inside a sentence is a description that mentions a ticket,
# and must keep generating from the tester's own words. Never raises.
#
# THE INVARIANT (round 3, after two rounds of moving this defect around):
# **casing is not evidence.** Round 2 required the token to be uppercase, so
# that `Phase-2` and `iOS-17` would not be mistaken for tickets -- and
# `TICKET-7154` promptly generated a 96-case suite about seven characters,
# which is audit F8 verbatim. The same module already treats `TICKET-1` as a
# key everywhere else, so a case rule here contradicts the rest of the file.
# What actually resolves the ambiguity is that this branch ASKS: recognising
# a key-SHAPED whole input costs one clarify turn and no fetch, and the
# clarify offers both readings, so a false positive is cheap and a false
# negative ships an ungrounded suite. Recognition is therefore
# case-INSENSITIVE and the caller must DISCLOSE rather than act.
def bare_issue_key(value: object) -> str:
    """The issue key when *value* is a BARE key and nothing else, else ""."""
    if not isinstance(value, str):
        return ""
    return _valid_issue_key(value.strip())


def _configured_base_url() -> str:
    """The configured Jira base URL, stripped, or "" when settings cannot supply one."""
    try:
        return str(settings_port.get().jira_base_url or "").strip().rstrip("/")
    except (
        AttributeError,
        TypeError,
        ValueError,
    ):  # pragma: no cover - settings is lenient
        return ""


def _base_refused(base: str) -> bool:
    """True when *base* is not http(s) or carries embedded credentials."""
    if not base.lower().startswith(("http://", "https://")):
        return True
    try:
        parsed = urlparse(base)
        return bool(parsed.username or parsed.password or "@" in (parsed.netloc or ""))
    except ValueError:  # pragma: no cover - urlparse on an http(s) string
        return True


# ``<JIRA_BASE_URL>/browse/<KEY>`` for a valid key, or "" when the key is
# not a key or no base URL is configured.
#
# The base URL is an install setting, never host-submitted, and the key has
# already passed :func:`_valid_issue_key`, so the result cannot carry a
# traversal or a separator. Never raises.
#
# Round-3 review (L2): a base carrying EMBEDDED CREDENTIALS
# (``https://user:pw@host``) is refused, because as of round 2 this URL is
# printed to the tester and a password does not belong in chat. An operator
# configuring credentials here has bigger problems, but a value that would
# leak one must not be echoed.
def issue_url_for_key(key: object) -> str:
    """``<JIRA_BASE_URL>/browse/<KEY>`` for a valid key, or "" otherwise."""
    valid = _valid_issue_key(key if isinstance(key, str) else "")
    if not valid:
        return ""
    base = _configured_base_url()
    if _base_refused(base):
        return ""
    return f"{base}/browse/{valid}"


def selected_issue_key(url: str) -> str:
    """Ticket key carried in a board/backlog URL's query string, else "".

    Testers copy URLs like .../boards/1276?selectedIssue=KEY-1 straight from the
    board, and that path has no /browse/ segment. Never raises.
    """
    try:
        params = parse_qs(urlparse(url).query)
        for name in ("selectedIssue", "selectedIssueKey"):
            for value in params.get(name, []):
                key = _valid_issue_key(value)
                if key:
                    return key
    except (ValueError, TypeError, AttributeError):
        logger.debug("selected_issue_key: parse failed", exc_info=True)
    return ""


def issue_key_from_url(url: str) -> str:
    """Best-effort Jira issue key for *url* ("" when there isn't one).

    Handles ``/browse/KEY-1``, ``/issues/KEY-1``, and board/backlog URLs that
    carry ``?selectedIssue=KEY-1``. Never raises.
    """
    with never_raise(logger, "issue_key_from_url: parse failed", exc_info=True):
        parsed = urlparse(url)
        path = (parsed.path or "").rstrip("/")
        for marker in ("/browse/", "/issues/"):
            if marker in path:
                key = _valid_issue_key(path.split(marker, 1)[1].split("/", 1)[0])
                if key:
                    return key
        return selected_issue_key(url)
    return ""
