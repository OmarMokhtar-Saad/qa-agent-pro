"""Verdict headline and update-rate-limit advisory for the qa-doctor report."""

from __future__ import annotations

import sys
from typing import NamedTuple

from handlers.seam_alias import alias


class VerdictCounts(NamedTuple):
    """What the Action items will say: blockers, warnings, unverified checks."""

    blockers: int
    recommended: int
    unverified: int


# The qa-doctor line for an update check GitHub refused on quota.
#
# D4 (2026-08-21, TICKET-5138) shipped the detection half: run_update_check
# returns "rate-limited" instead of folding a spent quota into "error", so a
# 403 is no longer indistinguishable from a DNS failure in the log. Nothing
# told the TESTER, which was the half that mattered -- the live Cursor run at
# 21:33:55 logged the 403 and started the version already on disk, so the
# install was silently BLIND to a newer release with the remedy nowhere in
# front of anyone.
#
# Returns "" for every other status, so it is used as a guard exactly like
# _embeddings_backend_advisory above: a line is printed only where the loss is
# real. RECOMMENDED rather than a blocker -- generation, export and every
# other tool are unaffected, and the quota is per hour, so this cannot make an
# otherwise healthy install report "Not ready". It is deliberately NOT
# `optional` either: unlike the always-true retirement note above, this fires
# only on a run whose quota really was spent, so "Ready, with warnings" is
# accurate for that run and gone by the next one.
#
# No try/except, unlike its neighbour: this reads no module and no setting, so
# there is no failure mode to swallow -- and therefore no defensive branch that
# would sit uncovered in a directory held at 90%. Never raises.
def update_rate_limit_advisory(status: str) -> str:
    """The qa-doctor line for an update check GitHub refused on quota."""
    if status != "rate-limited":
        return ""
    return (
        "GitHub RATE-LIMITED this install's update check, so auto-update is "
        "blind right now: the server could not see whether a newer release "
        "exists and is running the version already on disk. This is not a "
        "network failure -- GitHub was reached and refused on quota. No action "
        "is needed to recover: the quota is per hour and clears by itself, so "
        "the next check succeeds. Set `GITHUB_TOKEN` in the install's `.env` "
        "only if you keep seeing this -- an unauthenticated check shares 60 "
        "requests/hour with everything else on this machine, a token gets 5,000."
    )


def _unverified_text(unverified_count: int) -> str:
    return (
        "⚠️ **Ready — one check unverified**, see Action items below"
        if unverified_count == 1
        else f"⚠️ **Ready — {unverified_count} checks "
        "unverified**, see Action items below"
    )


def _head_text(counts: VerdictCounts) -> str:
    """The headline branch ladder: blockers, then warnings, then unverified."""
    blocker_count, recommended_count, unverified_count = counts
    if blocker_count:
        return (
            f"❌ **Not ready** — {blocker_count} blocking issue(s), "
            "see Action items below"
        )
    if recommended_count:
        return "⚠️ **Ready, with warnings** — see Action items below"
    if unverified_count:
        return _unverified_text(unverified_count)
    return "✅ **Ready** — all required checks passed"


# The qa-doctor headline, decided from what the Action items will SAY.
#
# Pure and total on purpose (D-E1, 2026-09-01). The verdict used to be an
# inline if/elif over ``blockers``/``recommended`` while a third category --
# the unconditional Atlassian probe item -- was appended to ``items``
# downstream of it, so "all required checks passed" shipped four lines above
# an open "Fix now" entry on every run, healthy installs included. A headline
# that its own report contradicts trains the reader to skip the headline.
#
# ``unverified`` (third field of ``counts``) is the third category: checks this
# server cannot settle from here and has asked the calling agent to settle for
# it. They are not blockers (nothing is known to be broken) and not warnings
# (no action is recommended to the TESTER), so neither existing branch fits.
#
# `limited` is APPENDED, never counted. It names what this machine cannot do --
# a missing optional tool, a Jira connection known not to work, an Android
# lane switched off -- and it must not be able to move the branch above:
# `adb` is not needed to generate a single test case, so an install without
# it is READY, and saying otherwise would train the reader to ignore the
# one line the report leads with. What was wrong before is narrower and is
# fixed here: the headline said "Ready" and never named what had stopped
# working, so a machine with no Android tooling and a dead Jira read
# exactly like a healthy one.
def overall_verdict(counts: VerdictCounts, *, limited: tuple[str, ...] = ()) -> str:
    """The qa-doctor headline, decided from what the Action items will SAY."""
    head = _head_text(counts)
    if not limited:
        return head
    return head + " · Not available on this machine: " + "; ".join(limited)


_overall_verdict = alias(sys.modules[__name__], "overall_verdict")
_update_rate_limit_advisory = alias(sys.modules[__name__], "update_rate_limit_advisory")
