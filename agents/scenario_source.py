"""Source grounding and scope of a prepared generation."""

from __future__ import annotations

from typing import NamedTuple

from agents.scenario_html import _strip_html
from agents.scenario_scope import _scope_feature_text
from agents.scenario_scope_directive import (
    _build_parent_scope_directive,
    _build_source_scope_directive,
)
from agents.scenario_urls import _find_product_urls, _is_jira_ticket_url


# _unreadable_source_refusal: refuse to fabricate when a URL was the source but
# could not be read and no real feature text was supplied. Without this guard an
# unreadable ticket (auth wall / JS SPA) yields confident but invalid test cases.
#
# 2026-08-15 (batch D0): the fail-fast backend preflight that stood in
# _prepare_generation was REMOVED. It resolved a backend and refused the whole
# prepare when none resolved, protecting a server-side fan-out that has not run
# since generation became chat-only (llm.resolve_generation_mode() returns the
# "host" constant). The live host prepare makes no server-side LLM call, so the
# guard broke every keyless install. Do NOT re-add a resolver call; a caller that
# needs a backend must preflight at its OWN call site. Regression cover:
# tests/test_keyless_prepare_regression.py.
def _unreadable_source_refusal(
    url_content: dict | None, feature_text: str
) -> tuple[str, str, str, str, str] | None:
    stripped_feature = (feature_text or "").strip()
    feature_is_bare_url = stripped_feature.lower().startswith(("http://", "https://"))
    if not (
        url_content
        and url_content.get("error")
        and (not stripped_feature or feature_is_bare_url)
    ):
        return None
    msg = (
        "I couldn't read the provided ticket, so I won't generate test cases "
        "from an empty source.\n\n"
        f"**Reason:** {url_content['error']}\n\n"
        "Please paste the ticket's description and acceptance criteria, and I'll "
        "generate test cases grounded in the real content."
    )
    return (msg, "", "", "", "error")


class _SourceScope(NamedTuple):
    """The feature text as finally scoped, plus what was derived from the source."""

    feature_text: str
    source_url: str
    nav_scope_directive: str
    parent_context: str
    parent_scope_directive: str


def _bare_source_url(feature_text: str) -> str:
    """The pasted feature when it is nothing but an http(s) URL, else ""."""
    stripped = (feature_text or "").strip()
    return stripped if stripped.lower().startswith(("http://", "https://")) else ""


def _grounded_title(url_content: dict) -> str:
    """The fetched ticket's title, else the first 200 chars of its text."""
    grounded = (url_content.get("title") or "").strip()
    if not grounded:
        grounded = _strip_html(url_content.get("raw_text", "") or "")[:200].strip()
    return grounded


# (source_url, nav_scope_directive, feature_text) for a bare Jira URL input.
#
# TICKET-7154 Fix 1: a bare Jira/issue SOURCE URL must never become the
# app-under-test navigation target. When the pasted feature IS a bare Jira
# ticket URL and its content was fetched, ground the feature in the ticket
# TITLE/content (never the URL) and build a directive that forbids the model
# from writing "Navigate to <jira url>" and steers it to click-paths or
# artifact-review framing when the ticket names no product URL.
def _ground_jira_source(
    feature_text: str, url_content: dict | None
) -> tuple[str, str, str]:
    """(source_url, nav_scope_directive, feature_text) for a bare Jira URL input."""
    source_url = _bare_source_url(feature_text)
    if not (
        source_url
        and _is_jira_ticket_url(source_url)
        and url_content
        and not url_content.get("error")
    ):
        return source_url, "", feature_text
    product_urls = _find_product_urls(url_content, source_url)
    directive = _build_source_scope_directive(source_url, product_urls)
    return source_url, directive, _grounded_title(url_content) or feature_text


# The parent story of a Jira sub-task, as markup-free background text.
#
# tools/jira_fetcher._build_parent_context composed the parent story into its
# OWN key. It is never merged into raw_text/description: _find_product_urls
# scans those, and a link inside somebody else's story must never become this
# ticket's navigation target (TICKET-7154).
#
# It goes through the SAME _strip_html as the description and the acceptance
# criteria (F10, 2026-08-15: a real ticket handed the generator raw markup on
# TICKET-5645). Deliberately _strip_html and NOT a blanket r"<[^>]+>": that
# deleted every <Field name> placeholder in a Jira UC table.
def _parent_context_text(url_content: dict | None) -> str:
    """The parent story of a Jira sub-task, as markup-free background text."""
    if not url_content or url_content.get("error"):
        return ""
    return _strip_html(str(url_content.get("parent_context", "") or "")).strip()


# Ground and scope the feature text and pull the parent story from the source.
#
# A bare URL is a weak feature spec: it is grounded in the fetched page title +
# extracted UI so the fan-out stays on THIS page. No-op for real descriptions.
# Every use of parent_context is guarded by a truthy value, so
# JIRA_FETCH_PARENT=false restores the previous behaviour end to end.
#
# Ticket COMMENTS never reach this agent: the reconciled-amendments block
# (tools/comment_reconciler, url_content["amendments_context"]) was deleted in
# dead-code batch D5 (2026-08-15). A revival must restore the module, the read,
# the prompt section AND the containment control that sanitised the block --
# docs/RETIRED_CAPABILITIES.md section 4.
def _resolve_source_scope(
    feature_text: str, url_content: dict | None, ui_content: dict | None
) -> _SourceScope:
    """Ground and scope the feature text and pull the parent story from the source."""
    source_url, nav_directive, grounded = _ground_jira_source(feature_text, url_content)
    scoped = _scope_feature_text(grounded, url_content, ui_content)
    parent_context = _parent_context_text(url_content)
    parent_directive = _build_parent_scope_directive(scoped) if parent_context else ""
    return _SourceScope(
        scoped, source_url, nav_directive, parent_context, parent_directive
    )
