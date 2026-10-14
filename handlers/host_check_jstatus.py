"""Jira status row for the host-check report."""

from __future__ import annotations

import sys

from handlers import generate
from handlers.seam_alias import alias

_JIRA_STATUS_TAIL = (
    "Jira is read through YOUR Atlassian MCP connection (OAuth, Jira "
    "Cloud) \u2014 nothing to configure on this machine."
)

_JIRA_STATUS_UNCHECKED = (
    "{head}\u26a0\ufe0f not checked yet. {tail} I check it the first time "
    "you ask me for a Jira ticket, not before \u2014 it's probably fine; if "
    "you're not signed in then, I'll walk you through authorizing it."
)

# Placeholders: {head} {age} {tail}; any other state falls back to UNCHECKED.
_JIRA_STATUS_TEMPLATES: dict[str, str] = {
    "verified": "{head}\u2705 checked and working ({age} ago). {tail}",
    "stale": (
        "{head}\u26a0\ufe0f checked {age} ago and not re-checked since. "
        "{tail} An OAuth session can expire or be revoked in that time, so "
        "I'll re-check it the next time you ask me for a Jira ticket."
    ),
    "access_unconfirmed": (
        "{head}\u2705 signed in ({age} ago) \u2014 but I could not confirm "
        "it can reach your Jira site. {tail} A ticket URL may still fail; "
        "if one does, that is the first thing to check."
    ),
    "probe_outdated": (
        "{head}\u26a0\ufe0f checked {age} ago, but only that you were "
        "signed in \u2014 not that the account can reach your Jira site. "
        "{tail} I'll re-check it the next time you ask "
        "me for a Jira ticket."
    ),
    "wrong_site": (
        "{head}\u274c signed in ({age} ago), but to an Atlassian account "
        "that cannot reach your Jira site. {tail} Ticket URLs will fail "
        "until you connect the account that has it -- run "
        "`qa_configure_jira` and I'll show the exact steps for your client."
    ),
    "failed": (
        "{head}\u274c the last check ({age} ago) did not come back "
        "connected. {tail} Paste a ticket URL, or run `qa_configure_jira`, "
        "and I'll show the exact connection steps for your client."
    ),
}


def jira_status_text(state: str, view: dict | None) -> str:
    """The Integrations row for Jira, as one of four states.

    Every other row in that table leads with a state glyph -- Python, Excel
    auto-export, and each feature gate -- so the Jira row's blankness was the
    only cell in a table of verdicts that carried none, and absence reads as
    "nothing to flag". This server genuinely cannot see the tester's Atlassian
    OAuth session, so the honest default is "not checked yet", never "working"
    and never "broken".

    Pure and total: no store read, no clock, no settings, so freshness is
    decided once by the caller and never re-derived here. Never raises.
    """
    template = _JIRA_STATUS_TEMPLATES.get(state, _JIRA_STATUS_UNCHECKED)
    return template.format(
        head="\U0001f517 **Jira** \u2014 ",
        age=_jira_status_age(view),
        tail=_JIRA_STATUS_TAIL,
    )


def _jira_status_age(view: dict | None) -> str:
    """Age label for the Jira row; days past 48 hours, else _ago_label."""
    # _ago_label tops out at hours, and "stale" means a WEEK by definition, so
    # the state this matters most for would have read "168 hour(s) ago". Days
    # are formatted here rather than in the shared helper, whose other callers
    # report minutes-old preps and want its existing behaviour.
    _age_s = float((view or {}).get("age_s") or 0)
    if _age_s >= 48 * 3600:
        _days = int(_age_s // (24 * 3600))
        return f"{_days} day{'s' if _days != 1 else ''}"
    return generate._ago_label(_age_s)


_jira_status_text = alias(sys.modules[__name__], "jira_status_text")
