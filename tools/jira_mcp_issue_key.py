"""Jira MCP: issue key helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

from typing import NamedTuple

from tools.jira_mcp_keys import _valid_issue_key, issue_key_from_url
from tools.jira_mcp_nodes import _issue_node


class IssueRef(NamedTuple):
    """The resolved issue key and whether the URL's key was only assumed."""

    key: str
    assumed_from_url: bool


def _issue_key_claim(issue: object, source_url: str) -> tuple[str, str, str]:
    """``(raw_payload_key, payload_key, url_key)`` for the identity guard."""
    # The key is read from the SAME node the fields came from (see
    # _issue_node): on the live `{"issues": {"nodes": [...]}}` envelope the
    # outer dict has no `key` at all, and reading it there made the guard
    # in _check_issue_identity unreachable for exactly the payload shape the
    # host really sends.
    _key_node = _issue_node(issue)
    # Presence is decided BEFORE any truthiness collapse: `or ""` turned
    # `key: 0` / `False` / `[]` / `{}` -- present and unreadable, state 3 --
    # into state 1 and let them through (round-3 review).
    _raw_key_value = (_key_node or {}).get("key")
    _raw_payload_key = "" if _raw_key_value is None else str(_raw_key_value).strip()
    payload_key = _valid_issue_key(_raw_payload_key)
    return _raw_payload_key, payload_key, issue_key_from_url(source_url)


def _unreadable_key_result(url_key: str) -> dict:
    """Refusal: the payload's ``key`` is present but not a readable issue key."""
    return {
        "error": (
            "⚠️ I can't tell which ticket that Jira payload is "
            f"for. The URL names `{url_key}`, but the JSON's own `key` "
            "field is not a readable Jira issue key, so I cannot check "
            "that the two agree -- and generating anyway would stamp "
            f"`{url_key}` on requirements that may come from a different "
            "ticket. Call `getJiraIssue` again and pass its RAW, "
            "unmodified result as `jira_content_json`."
        ),
        "content": None,
        "needs_jira_mcp": False,
    }


def _key_mismatch_result(url_key: str, payload_key: str) -> dict:
    """Refusal: the payload is for a different ticket than the link."""
    return {
        "error": (
            "⚠️ That Jira payload is for a different ticket "
            f"than the link. The URL names `{url_key}`, but the JSON "
            f"handed back is `{payload_key}`. I won't generate from a "
            "ticket the tester didn't ask for, and I can't tell which "
            "one they meant. If the ticket you want is "
            f"`{payload_key}`, start again with ITS url and pass this "
            "same JSON. If you want "
            f"`{url_key}`, re-run `getJiraIssue` for `{url_key}` and "
            "pass THAT raw JSON -- but note that an issue MOVED between "
            f"projects keeps its old key as an alias, so if `{url_key}` "
            f"keeps coming back as `{payload_key}` then `{payload_key}` "
            "is the live key and the first route is the one to take."
        ),
        "content": None,
        "needs_jira_mcp": False,
    }


# 2026-09-02 audit F5. The host fetches the ticket with its OWN
# Atlassian MCP connection and hands the JSON back, so nothing but this
# comparison ties the payload to the URL the tester actually gave. When
# they disagree the whole downstream reply is wrong in a way no tester
# can see: the ticket key, the export filename and the traceability
# table come from one issue while every requirement in the prompt comes
# from the other. There are THREE states here, and collapsing the last
# two is what the first cut of this guard got wrong (round-2 review, H2):
#   absent       -- no `key` field at all. A trimmed result; the URL's
#                   key remains the only claim on the record. Proceed.
#   readable     -- compare it. Disagreement is a refusal.
#   unreadable   -- a `key` IS present but does not parse as an issue
#                   key (`TICKET_9999`, an 11-digit number, a non-ASCII
#                   digit). Treating that as absent meant the payload
#                   claimed one ticket, the record named another, and
#                   nothing compared them -- silently, which is the
#                   whole defect. It is unverifiable, so refuse.
# Refuse by NAME rather than pick a winner in either case: only the
# tester knows which ticket they meant.
def _claim_vs_fields(raw_key: str, payload_key: str, url_key: str) -> dict | None:
    """A refusal dict when the payload's key claim and the URL disagree."""
    if raw_key and not payload_key and url_key:
        return _unreadable_key_result(url_key)
    if payload_key and url_key and payload_key != url_key:
        return _key_mismatch_result(url_key, payload_key)
    return None


# A refusal dict, or ``(key, key_assumed_from_url)`` when the payload may proceed.
def _check_issue_identity(issue: object, source_url: str) -> dict | IssueRef:
    """A refusal dict, or an :class:`IssueRef` when the payload may proceed."""
    raw_key, payload_key, url_key = _issue_key_claim(issue, source_url)
    refusal = _claim_vs_fields(raw_key, payload_key, url_key)
    if refusal is not None:
        return refusal
    # 2026-09-02 verification of audit F5: the ABSENT state above proceeds by
    # design (a trimmed result must not be refused), but it proceeded
    # SILENTLY -- the reply then named the URL's key as if the payload had
    # confirmed it. Record the assumption so the grounding note can say so.
    return IssueRef(payload_key or url_key, bool(url_key) and not payload_key)
