"""Jira image gate: the one-line note naming what a suite is grounded on."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from handlers import jira
from tools.jira_mcp import _valid_issue_key

logger = logging.getLogger("tools.mcp_handlers")


def _openapi_note(source_text: str) -> str:
    """Return the grounding note for an OpenAPI/Swagger spec source."""
    where = jira._safe_host(source_text)
    return (
        "> \U0001f9ed Grounded on the **OpenAPI/Swagger spec**"
        + (f" at `{where}`" if where else "")
        + "."
    )


def _web_page_note(source_text: str) -> str:
    """Return the grounding note for an ordinary web page source."""
    where = jira._safe_host(source_text)
    return (
        "> \U0001f9ed Grounded on the **web page**"
        + (f" at `{where}`" if where else " you gave me")
        + " -- not a Jira ticket. If you meant a ticket, paste its "
        "`/browse/KEY-123` url and I'll read that instead."
    )


# One line naming WHAT the suite will be generated from.
#
# 2026-09-01. The prepare reply disclosed a great deal -- images it could
# not read, the ticket snapshot, condensed payloads -- but never the one
# fact that would have caught the worst defect found so far: a Jira BOARD
# url was scraped as an ordinary web page and its ``<title>`` (the single
# word "Jira") became the whole feature description, with eight categories
# generated from it. Nothing in the reply was false; the source was simply
# never named, so the error was invisible until someone read the cases.
#
# Static, derived from what grounding already returned -- no fetch, no
# model call. Never raises: a note is a disclosure, and failing to build
# one must never cost the tester their suite.
def _grounding_source_note(
    source_text: str, url_content: dict | None, openapi_text=None
) -> str:
    """Return one line naming what the suite will be generated from."""
    with never_raise(logger, "grounding source note failed", exc_info=True):
        content = url_content or {}
        if openapi_text:
            return _openapi_note(source_text)
        if content.get("source") == "atlassian_mcp":
            return _grounding_note_atlassian(content)
        if content.get("content"):
            return _web_page_note(source_text)
        return (
            "> \U0001f9ed Grounded on the **feature description you gave me** "
            "-- no ticket and no page were read."
        )
    return ""


def _grounding_note_atlassian(content: dict) -> str:
    """The grounding note for a ticket read through the host's Atlassian MCP."""
    key = _valid_issue_key(content.get("issue_key"))
    named = f"Jira ticket `{key}`" if key else "the Jira ticket"
    note = (
        f"> \U0001f9ed Grounded on {named}, read through YOUR Atlassian "
        "MCP connection -- this server never fetched it."
    )
    # Audit F5 (verified open 2026-09-02): a payload with NO `key`
    # proceeds by design, but the reply used to name the link's key as
    # if the payload had confirmed it. Say that it was assumed.
    if key and content.get("issue_key_from_url"):
        note += (
            " The JSON handed back carried no `key` field, so the key "
            "is taken from the link -- if the ticket you meant is a "
            "different one, re-run `getJiraIssue` and pass its raw result."
        )
    return note
