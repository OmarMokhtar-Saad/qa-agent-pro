"""Jira MCP: verify render helpers (split from tools/jira_mcp.py)."""

from __future__ import annotations

from typing import cast

from tools.jira_mcp_access import access_verdict
from tools.jira_mcp_connect import connect_steps
from tools.jira_mcp_errors import _NO_IDENTITY, _error_text
from tools.jira_mcp_sites import _render_sites
from tools.jira_mcp_tool_names import access_tool_name

# Distinct from the others, and the difference matters to the reader: for
# "inferred" the list WAS read. It just was not labelled, so treating a miss as
# proof would be accusing a tester on our own inference.
_STATIC_REASONS = {
    "unreadable": (
        "the list of Atlassian sites came back in a shape I could not "
        "read, so I checked the sign-in only"
    ),
    "inferred": (
        "your editor sent the site list as bare text rather than "
        "labelled entries -- I could read it, but not safely enough to "
        "tell you your site is missing, so I checked the sign-in only"
    ),
    "no_host": (
        "no Jira host is configured here to compare against -- set "
        "`JIRA_BASE_URL` in the install's `.env` and I can confirm this "
        "account reaches YOUR site, which is what catches a sign-in on "
        "a personal Atlassian account"
    ),
}


def _no_access_reason(access: str, sites: object) -> str:
    """Why a proven sign-in's site access went unchecked, for one
    ``access_verdict`` that is neither a match nor a miss."""
    # NOT in the dict below: _error_text(None) logs a traceback, and sites is
    # None on every identity-only hand-back.
    if access == "sites_error":
        return "that list could not be read: " + (
            _error_text(cast("dict", sites)) or _NO_IDENTITY
        )
    return {
        **_STATIC_REASONS,
        "not_supplied": (
            "your editor did not send the list of Atlassian sites this "
            f"account can reach -- if `{access_tool_name()}` is in your "
            "tool list, call it too and send both results together"
        ),
    }.get(access, "I could not check which Atlassian sites it can reach")


# The kept/not-kept sentence is shared by the success shapes. Its wording is
# load-bearing twice over: handle_configure_jira REPLACES the last sentence
# when the store refuses the write (4ef175a0), so the two must be edited
# together.
_VERIFY_KEPT = (
    "This server holds no Jira credential and did NOT keep that identity "
    "-- no account id, email, name or site address. It records only that "
    "a check succeeded, when, and which tool was called, so `qa-doctor` "
    "stops asking again for the next week."
)


def _wrong_site(who: str, sites: object) -> tuple[str, str]:
    """The verdict for a proven identity whose sites miss the Jira host."""
    reachable = _render_sites(sites) or "no Atlassian site at all"
    return (
        "wrong_site",
        f"❌ **Connected as {who} -- but to the wrong Atlassian "
        f"account.** That sign-in can reach {reachable}, and your Jira "
        "site is not among them, so ticket URLs will fail until you "
        "connect the account that has it.\n\n" + connect_steps(),
    )


def _verified_no_access(who: str, access: str, sites: object) -> tuple[str, str]:
    """The verdict for a proven identity whose site access went unchecked."""
    return (
        "verified_no_access",
        f"✅ **Signed in to Atlassian** as {who} -- but I could not "
        "confirm it can reach your Jira site, because "
        f"{_no_access_reason(access, sites)}.\n\nTicket "
        "URLs will probably work; if they do not, this is the first thing "
        "to check. " + _VERIFY_KEPT,
    )


def _render_verified(who: str, sites: object) -> tuple[str, str]:
    """The success phase of ``verify_outcome``: the verdict for a proven
    identity *who*, by what *sites* says about access. No own ``try``."""
    access = access_verdict(sites)
    if access == "match":
        return (
            "verified",
            f"✅ **Atlassian verified** -- connected as {who}, and that "
            "connection can reach your Jira site.\n\nTicket URLs will be "
            "read through it, with your own Jira permissions. " + _VERIFY_KEPT,
        )
    if access in ("no_match", "empty"):
        return _wrong_site(who, sites)
    return _verified_no_access(who, access, sites)
