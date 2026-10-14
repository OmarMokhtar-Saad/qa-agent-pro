"""The disclosure clauses of the terms sentence: defaults, stops, budget and charter."""

from __future__ import annotations

from tools.mobile import explore_stop
from tools.mobile.charter_policy import budget_cap, budget_for, guard_policy
from tools.mobile.charter_schema import REFUSE, WIRED_STOPS
from tools.mobile.charter_scope import _scope_phrase


def _defaults_clause(assumed: list) -> str:
    """Which defaults the server assumed; ``""`` when none."""
    if not assumed:
        return ""
    return (
        "The tester did not set "
        + ", ".join(assumed)
        + ", so the server used its own default for "
        + ("each" if len(assumed) > 1 else "it")
        + ". "
    )


def _stop_clauses(terms: dict) -> str:
    """The undetected-stop and guard-refusal disclosures."""
    said = ""
    if terms["stop_on"] not in WIRED_STOPS:
        said += (
            terms["stop_on"] + " is recorded but not yet detected, so this run "
            "stopped on its budget instead. "
        )
    if guard_policy(terms["destructive"]) == REFUSE:
        said += (
            "This charter asks for a guard hit to END the attempt, and it "
            "does: a stopped action ends this run by name rather than pausing "
            "for the tester. "
        )
    return said


def _spend_phrase(budget: dict) -> str:
    """What the tester asked to spend: steps and minutes, or the lane's own."""
    return (
        (
            str(budget["steps"]) + " steps"
            if budget["steps"]
            else "the lane's own step budget"
        )
        + " and "
        + (
            str(budget["minutes"]) + " minutes"
            if budget["minutes"]
            else "the lane's own time budget"
        )
    )


# THE BUDGET THE RUN HAD, not only the one it was ASKED for. ``new_state``
# clamps the ask DOWNWARD through :func:`budget_for`, so a charter asking
# for 100 steps and 60 minutes runs on the lane's 30 turns and 20 minutes
# -- measured. This SENTENCE is a second consumer of the same fact and it
# named the ask alone, which is a false statement about the run the tester
# just had. M6/M6b grade the clamp in ISOLATION, which is exactly why the
# sentence stayed wrong. Both numbers are rendered: either alone is the
# worse artifact.
def _clamped_axes(terms: dict) -> list:
    """The axes the lane's ceiling clamped below the ask (empty when none)."""
    asked_steps, asked_seconds = budget_cap(terms)
    ran_steps, ran_seconds = budget_for(
        terms, explore_stop.MAX_TURNS, explore_stop.DEADLINE_S
    )
    clamped = []
    if asked_steps and ran_steps < asked_steps:
        clamped.append(str(ran_steps) + " turns")
    if asked_seconds and ran_seconds < asked_seconds:
        clamped.append(str(ran_seconds // 60) + " minutes")
    return clamped


# ONLY THE AXIS THAT ACTUALLY CLAMPED. Naming BOTH whenever EITHER
# clamped printed the TESTER'S OWN number back at them as "the lane's
# ceiling": measured, an ask of 100 steps and 5 minutes rendered
# "clamped to the lane's ceiling of 30 turns and 5 minutes" while the
# lane's time ceiling is 20 minutes and the run got the tester's 5 --
# a false sentence in the artifact that reconstructs the run, and a
# reintroduction of the very class the ask-versus-had fix above
# closed. Graded by M6e against a FOUR-FIXTURE MATRIX (steps-only,
# minutes-only, both, neither) built BEFORE this rule, because
# fixture diversity and not mutation count is what grades it.
def _budget_clause(terms: dict) -> str:
    """The budget sentence: what was asked, what clamped, what stops the run."""
    spend = _spend_phrase(terms["budget"])
    clamped = _clamped_axes(terms)
    if clamped:
        spend += ", clamped to the lane's ceiling of " + " and ".join(clamped)
    return "Budget " + spend + "; stopping on " + terms["stop_on"] + ". "


def _destructive_phrase(terms: dict) -> str:
    """Whether a guard stop ends the run or pauses it for the tester."""
    return (
        " (a guard stop ENDS the run)"
        if guard_policy(terms["destructive"]) == REFUSE
        else " (a guard stop pauses the run for the tester)"
    )


# THE DISCLOSURE THESE THREE FIELDS USED TO CARRY IS GONE, because the gap
# it described is gone: the turn packet carries the depth directive and
# the scope lines, and the destructive policy decides what happens after a
# guard hit. A disclosure that outlives the gap it described is the same
# defect as one that preceded the fix, so it was retired in the SAME
# commit that wired them. What is still genuinely unwired keeps its own
# sentence -- see the ``coverage_plateau`` clause above, untouched.
def _charter_clause(terms: dict) -> str:
    """The closing clause: depth, destructive policy and scope."""
    return (
        "Charter: depth "
        + terms["depth"]
        + " -- the turn packet asks for that work by name; destructive "
        + terms["destructive"]
        + _destructive_phrase(terms)
        + "; scope "
        + _scope_phrase(terms["scope"])
        + " -- the packet carries those lines, and a screen matching an "
        "excluded flow is RECORDED as an off-charter visit, though nothing "
        "stops the run on one. A resume reads these back and explores under "
        "them."
    )
