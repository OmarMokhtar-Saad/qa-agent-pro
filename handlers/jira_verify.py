"""Return half of the live Jira connection check: parse the hand-back, store the verdict."""

from __future__ import annotations

import logging

from tools import verify_store
from tools.jira_mcp import probe_provenance, verify_outcome

logger = logging.getLogger("tools.mcp_handlers")

# 2026-09-01: five statuses, not three. wrong_site is a NEGATIVE
# verdict -- the sign-in works and cannot reach this tester's Jira --
# so it persists as verified=False and reads as "failed" downstream.
# verified_no_access is a positive identity with the access half
# unchecked, recorded so the report can say which of the two it is.
_PERSISTABLE = {
    "verified": (True, True),
    "verified_no_access": (True, False),
    "wrong_site": (False, True),
    "failed": (False, False),
}


# The AGENT already made the read-only atlassianUserInfo call with its OWN
# Atlassian MCP connection, so all that is left here is parsing what it
# handed back. No LLM call, no HTTP request.
#
# 2026-09-01 (item A): the VERDICT is persisted, the payload is not.
# Only {verified, at, tool} reaches the store -- never the account
# id, the email, the display name, or any fragment of the raw JSON
# -- so the "we did not keep that identity" promise this reply makes
# still holds, while qa-doctor stops re-raising a question that was
# just answered (and can therefore reach "Ready" again).
#
# "unknown" establishes NOTHING and deliberately does not overwrite
# an earlier verdict: an agent that fumbles the hand-back must not
# be able to erase a good result.
#
# verify_store never raises -- it reports. A discarded report is
# how the reply came to promise a week of remembered state that
# a locked suites.db had not written.
async def _verified_jira_reply(verification: str) -> str:
    """Return half of the live check: parse the hand-back, persist the verdict."""
    status, message = verify_outcome(verification)
    if status not in _PERSISTABLE:
        return message

    _verified_flag, _site_checked = _PERSISTABLE[status]
    _stored = await verify_store.record_verdict(
        _verified_flag, probe_provenance(), _site_checked
    )
    if not (isinstance(_stored, dict) and _stored.get("content")):
        logger.warning(
            "verify_store: the verdict was not recorded (%s)",
            (_stored or {}).get("error"),
        )
        message = _verdict_unrecorded_message(message)
    return message


def _verdict_unrecorded_message(message: str) -> str:
    """The verified reply with its week-of-remembered-state promise withdrawn."""
    return message.replace(
        "It records only that a check succeeded, when, and "
        "which tool was called, so `qa-doctor` stops asking "
        "again for the next week.",
        "I could not write this result down, so `qa-doctor` "
        "will ask again -- the check itself still stands.",
    )
