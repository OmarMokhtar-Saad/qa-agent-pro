"""qa-doctor's Atlassian/Jira verdict and advisories (moved from setup_check)."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from handlers import host_check, setup_state
from tools import verify_store
from tools.jira_mcp import connect_hint_line

logger = logging.getLogger("tools.mcp_handlers")

_ON_DEMAND_RECHECK = (
    "The next Jira ticket request starts by checking the Atlassian "
    "connection and shows the sign-in steps if it is not connected."
)


# 2026-09-01 (item A): the verdict recorded by a previous
# qa_configure_jira probe, for THIS client. Read ONCE, here, because
# three separate sites need it -- the on-disk hint below, the
# Integrations row, and the report headline -- and a report whose three
# Jira statements disagree with each other is worse than one guess.
#
# Guarded: verify_store never raises, and this makes a broken read
# degrade to "never verified", which is exactly the pre-A behaviour. It
# must not reach this function's outer `except`, which would replace the
# ENTIRE report with a failure line over a verdict lookup.
async def _load_verdict_view() -> dict | None:
    _verdict_view = None
    with never_raise(logger, "atlassian verdict lookup skipped", exc_info=True):
        _verdict_view = (await verify_store.load_verdict()).get("content")
    return _verdict_view


# Provenance is checked BEFORE freshness: a day-old verdict from the
# identity-only probe and a week-old verdict from the current one need
# different words, and only the first can be fixed by re-running a
# better check.
def _atlassian_verdict_state(view: dict | None) -> str:
    """Name the recorded Atlassian verdict: provenance first, then freshness."""
    if not view:
        _verdict_state = "unverified"
    elif not view.get("verified"):
        # A negative verdict whose ACCESS half was checked means the sign-in
        # worked and reached the wrong Atlassian account -- a different
        # sentence and a different remedy from "not connected".
        _verdict_state = "wrong_site" if view.get("site_checked") else "failed"
    elif not view.get("current_probe"):
        _verdict_state = "probe_outdated"
    elif not view.get("fresh"):
        _verdict_state = "stale"
    elif not view.get("site_checked"):
        _verdict_state = "access_unconfirmed"
    else:
        _verdict_state = "verified"
    return _verdict_state


# BEFORE connect_hint_line, deliberately: that helper re-reads the config
# from disk, so a fresh write flips its message to "already configured on
# disk" instead of telling the tester to add what was just added.
# G5: Jira is checked, mentioned and (with fix=true) written ONLY when it
# is already in use. A tester with no Jira sees no Jira line, and fix=true
# writes nothing for them; settings or an entry or a verdict keep today's
# behaviour, fix path included.
# verify_offered: the on-disk hint returns "" rather than shrugging
# beside an answer this report has already given -- the Integrations
# row states the Jira state in every verdict, including "checked when
# you first ask for a ticket". The CONNECT wording (no entry on disk)
# is unaffected and still appears.
async def _atlassian_part(run: setup_state._SetupRun) -> tuple[bool, list[str]]:
    """Run the Atlassian check/fix when Jira is in use; queue its advice."""
    _jira_used = host_check.jira_in_use(run.workspace_roots, run.view)
    if _jira_used:
        _atlassian_lines, _atlassian_advisories = await host_check.atlassian_autofix(
            fix=run.fix
        )
    else:
        _atlassian_lines, _atlassian_advisories = [], []
    (run.recommended if run.fix else run.optional).extend(_atlassian_advisories)
    _hint = (
        connect_hint_line(workspace_roots=run.workspace_roots, verify_offered=True)
        if _jira_used
        else ""
    )
    if _hint:
        run.optional.append(_hint)
    return _jira_used, _atlassian_lines


def _wrong_site_text(view: dict | None) -> str:
    return (
        "The Atlassian account signed in here cannot reach this "
        "tester's Jira site "
        f"({(view or {}).get('at')}), so ticket URLs will fail "
        "until the right account is connected in your editor. Test "
        "generation from a typed feature description is unaffected. "
        + _ON_DEMAND_RECHECK
    )


def _failed_text(view: dict | None) -> str:
    return (
        "The Atlassian (Jira) connection failed its last check "
        f"({(view or {}).get('at')}), so ticket URLs will not "
        "work until it is reconnected in your editor. Test generation "
        "from a typed feature description is unaffected. " + _ON_DEMAND_RECHECK
    )


# 2026-09-25: qa-doctor NEVER asks the agent to probe Atlassian. The
# probe call is what opens the OAuth sign-in, and a readiness check must
# not push the tester through an authorization they did not ask for.
# The connection is checked on demand instead: the first time the
# tester asks for Jira data, build_fetch_directive's getJiraIssue call
# either works or falls through to connect_steps(). Known-broken states
# still warn, because they say something the tester can act on.
#
# This block is deliberately ABOVE the headline: the failed states
# append to `recommended`, which overall_verdict counts.
#
# Same branch, so ONE derivation with two consumers: the action
# item says what to do, `limited` makes the headline say what has
# stopped working.
#
# Known-broken and actionable to the TESTER. Not a blocker:
# generation from a typed feature description is unaffected.
def _jira_state_advisories(run: setup_state._SetupRun, state: str) -> None:
    if state == "wrong_site":
        run.limited.append(
            "importing Jira tickets (the connected Atlassian account "
            "cannot reach this tester's site)"
        )
        run.recommended.append(_wrong_site_text(run.view))
    elif state == "failed":
        run.limited.append(
            "importing Jira tickets (the Atlassian connection failed its last check)"
        )
        run.recommended.append(_failed_text(run.view))
