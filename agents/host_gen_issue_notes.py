"""Host mode: input-only refusals of a prepare (C3c).

Moved from handlers/generate.py. Each refusal reads no store and moves no
screen, so it returns before anything is touched. Calls that reach a handler
module go through the JiraHooks / ImageHooks passed in.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from agents.host_gen_args import InputArgs
from agents.host_gen_hooks import ImageHooks, JiraHooks
from config.settings_guard import never_raise
from tools import jira_mcp

logger = logging.getLogger("agents.host_mode")

_EMPTY_INPUT = (
    "Tell me what to build test cases for -- a feature description, a "
    "Jira/issue URL, a web page URL, or a Swagger/OpenAPI spec URL."
)


# PURE: no DNS lookup, no fetch, no settings write -- exactly the host/path
# test ``tools.jira_fetcher.fetch_url_content`` already makes, Confluence
# (``/wiki/``) exemption included. F22 (2026-09-02 audit): that refusal lives
# PAST the Jira image gate, so a board URL was first asked "how do I get this
# ticket's screens?" -- for a ticket that does not exist. Shape first.
def jira_page_without_issue_note(text: str) -> str:
    """Refusal when *text* is a URL on a Jira HOST naming no ISSUE, else ""."""
    with never_raise(logger, "_jira_page_without_issue_note failed", exc_info=True):
        candidate = (text or "").strip()
        if not candidate.lower().startswith(("http://", "https://")):
            return ""
        if jira_mcp.looks_like_jira_url(candidate):
            return ""
        if not jira_mcp.is_jira_host(candidate):
            return ""
        if "/wiki/" in (urlparse(candidate).path or ""):
            return ""
        return jira_mcp.jira_page_without_issue_message(candidate)
    return ""


def _key_with_url(key: str, url: str) -> str:
    return (
        f"⚠️ `{key}` looks like a Jira issue key, not "
        "a feature description -- and generating from the key alone "
        "would invent a suite about those characters rather than "
        "about the ticket. On this install that key is "
        f"<{url}>. Re-send THAT url and I'll read the ticket. "
        f"If `{key}` is really the name of the feature and not "
        "a ticket, describe the feature in a sentence or two and "
        "I'll work from that instead."
    )


def _key_without_base(key: str) -> str:
    return (
        f"⚠️ `{key}` looks like a Jira issue key, not "
        "a feature description -- and generating from the key alone "
        "would invent a suite about that text rather than about the "
        "ticket. This install has no `JIRA_BASE_URL` configured, so I "
        "can't turn the key into a link myself. Send the full ticket "
        "URL instead, or paste the ticket's text and I'll work from "
        "that."
    )


# 2026-09-02 audit F8: a bare issue key is not a feature description. Route it
# to the same Atlassian fetch directive a ticket URL gets, resolving it against
# JIRA_BASE_URL; with no base URL configured, ask for the link. Round-2 review
# (M1): this DISCLOSES rather than silently rewriting the input.
def bare_issue_key_refusal(text: str, jira: JiraHooks) -> str:
    """Refusal when *text* is a BARE issue key and nothing else, else "".

    The grammar matches the WHOLE input only. Never raises.
    """
    _bare_key = jira_mcp.bare_issue_key(text)
    if not _bare_key:
        return ""
    _bare_url = jira.issue_url_for_key(_bare_key)
    if _bare_url:
        logger.info("prepare: bare issue key %s read as a ticket", _bare_key)
        return _key_with_url(_bare_key, _bare_url)
    return _key_without_base(_bare_key)


# v1.97.0 cursor-hardening (item 1): jira_content_json is assembled from the
# per-fetch stage tray when the caller sent stage_token instead. An
# explicitly-supplied jira_content_json still wins.
def prepare_input_refusal(a: InputArgs, jira: JiraHooks) -> tuple[str, str]:
    """``(refusal, jira_content_json)`` for the input-only refusals. Never raises."""
    jira_content_json = a.jira_content_json
    if not a.text:
        return _EMPTY_INPUT, jira_content_json
    jira_content_json, stage_err = jira.resolve_content_json(
        jira_content_json, a.stage_token
    )
    if stage_err:
        return stage_err, jira_content_json
    return bare_issue_key_refusal(a.text, jira), jira_content_json


def open_prep_refusal(prep: dict) -> str:
    """The dismissible "a prep for this source is ALREADY open" clarify."""
    _mins = max(0, int(float(prep.get("age_s") or 0) / 60))
    _ago = f"{_mins} minute(s)" if _mins else "less than a minute"
    return (
        "⚠️ A preparation for this exact source is ALREADY open "
        f"(`{prep.get('prep_id', '?')}`, started {_ago} ago) and has "
        "not been finalized. Preparing again starts a SECOND full "
        "generation of the same ticket -- 8 categories of cases your "
        "chat model has to write twice -- and does not continue or "
        "replace the open one.\n\n"
        "To CONTINUE the open one, submit its categories against that "
        "prep_id (`qa_prep_status` shows what is still missing). To "
        "deliberately start over, call `qa_prepare_test_cases` again "
        "with `proceed_anyway=true`."
    )


def attested_image_count(
    attached_image_count: Any, attached_images: Any, image: ImageHooks
) -> int:
    """This call's ATTESTED screens: the host's count plus direct image bytes.

    A coercion failure fails OPEN on this channel -- 99 attested means no
    attested gap and no refusal from it -- because a refusal must never be
    triggered by a bug in its own precondition. Never raises.
    """
    # pragma: no cover - a coercion never breaks a prepare
    with never_raise(logger, "incoming-image check failed", exc_info=True):
        return image.clamped_count(attached_image_count, hi=99) + len(
            [i for i in list(attached_images or []) if i]
        )
    return 99
