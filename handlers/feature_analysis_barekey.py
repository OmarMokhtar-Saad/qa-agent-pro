"""The refusal for a bare issue key given as the Feature Analysis feature."""

from __future__ import annotations

import logging

from tools.jira_mcp import bare_issue_key, issue_url_for_key

logger = logging.getLogger("tools.mcp_handlers")


def _fa_bare_key_with_url(bare_key: str, bare_url: str) -> str:
    return (
        f"⚠️ `{bare_key}` looks like a Jira issue key, not a feature "
        "description -- analysing the key alone would report on those "
        "characters rather than on the ticket. On this install that key "
        f"is <{bare_url}>. Re-send THAT url and I'll read the "
        f"ticket. If `{bare_key}` is really the feature's name, "
        "describe it in a sentence or two instead."
    )


def _fa_bare_key_no_url(bare_key: str) -> str:
    return (
        f"⚠️ `{bare_key}` looks like a Jira issue key, not a feature "
        "description, and this install has no `JIRA_BASE_URL` configured, so "
        "I can't turn the key into a link myself. Send the full ticket URL "
        "instead, or paste the ticket's text and I'll analyse that."
    )


# The refusal for a BARE issue key given as the feature, else "".
#
# 2026-09-02 audit F8, round 2 (H3). The prepare handler learned that a BARE
# issue key is a ticket reference and not a feature; this entry point had
# not, and opened an analysis task whose whole "ticket text" was the eight
# characters of the key. Same input, same defect, same remedy -- fixing one
# call site of a class is not fixing the class.
# Only the modes that READ A TICKET: `mobile` analyses captured screens and
# never resolves a key, so a screen set named `LOGIN-2` is not a ticket
# reference there (round-3 review, L1).
def _fa_bare_key_reply(text: str, mode: str) -> str:
    bare_key = bare_issue_key(text) if mode in ("jira", "jira_mobile") else ""
    if not bare_key:
        return ""
    bare_url = issue_url_for_key(bare_key)
    logger.info("feature analysis: bare issue key %s read as a ticket", bare_key)
    if bare_url:
        return _fa_bare_key_with_url(bare_key, bare_url)
    return _fa_bare_key_no_url(bare_key)
