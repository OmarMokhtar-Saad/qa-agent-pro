"""The exploratory lane's one extension: grant it into the state, or refuse it by name.

Cut from ``tools.mobile.explore_runner``; ``apply_turn_result`` drives it.
Never imports the facade.
"""

from __future__ import annotations

from tools.mobile import charter as charter_mod
from tools.mobile.explore_runner_consts import (
    EXTENSION_S,
    EXTENSION_TURNS,
    MAX_EXTENSIONS,
)
from tools.mobile.explore_runner_notices import (
    EXTENSION_NOT_GRANTED,
    EXTENSION_NOT_GRANTED_CONTINUE,
    EXTENSION_REFUSAL,
    EXTENSION_SPENT,
    EXTENSION_SPENT_CONTINUE,
)
from tools.mobile.explore_runner_types import Budget
from tools.mobile.explore_stop import DEADLINE_S, MAX_TURNS, _now, stop_reason


def _budget_of(body: dict, now: float | None) -> Budget:
    turns = int(body.get("turns_budget") or MAX_TURNS)
    deadline = float(body.get("deadline") or _now(now))
    return Budget(turns, deadline)


# ``is not None``, never ``or``: ``started`` is 0.0 on
# every test clock and in any run whose epoch is 0, and
# the ``or`` form silently REBASES the deadline onto NOW
# -- lengthening the very run this clamp exists to bound.
# That is mutant M6c, killed by
# test_an_extension_cannot_outrun_the_charters_budget,
# which builds the state at now=0.0 and asserts the
# deadline stays at or below 120.0. Do not "tidy" it back.
def _started(body: dict, now: float | None) -> float:
    raw = body.get("started")
    return float(raw if raw is not None else _now(now))


# The turns budget and deadline an extension would give, charter-clamped.
#
# THE SECOND WRITER of this run's budget. ``new_state``
# clamped these two numbers through ``charter.budget_for``;
# this site ADDS to them, so "a charter can never lengthen a
# run" is false here unless it re-clamps. ``budget_cap`` is
# the one producer of what the tester asked for and returns 0
# where they asked for nothing, so a run with no charter
# extends exactly as it always has.
def _clamped_extension(body: dict, had: Budget, now: float | None) -> Budget:
    """The turns budget and deadline an extension would give, charter-clamped."""
    cap_turns, cap_seconds = charter_mod.budget_cap(body.get("charter"))
    turns = had.turns + EXTENSION_TURNS
    if cap_turns:
        turns = min(turns, min(cap_turns, MAX_TURNS))
    deadline = had.deadline + EXTENSION_S
    if cap_seconds:
        deadline = min(deadline, _started(body, now) + min(cap_seconds, DEADLINE_S))
    return Budget(turns, deadline)


def _granted_text(gained_turns: int, gained_seconds: float) -> str:
    """WHAT WAS ACTUALLY GRANTED, never the full figure.

    A PARTIAL grant (one axis moves, the other does not) names
    only the axis that moved, for the same reason the clamp
    sentence in ``charter.describe_terms`` names only the
    axis that clamped: printing a number the run did not get
    is a false statement about the run the tester just had.
    """
    granted = []
    if gained_turns > 0:
        granted.append(str(int(gained_turns)) + " turns")
    if gained_seconds >= 60:
        granted.append(str(int(gained_seconds // 60)) + " minutes")
    elif gained_seconds > 0:
        granted.append("under a minute")
    return " and ".join(granted)


def _refusal_notice(body: dict, reason: str, ending: bool) -> str:
    if int(body.get("extensions_used") or 0) >= MAX_EXTENSIONS:
        return EXTENSION_SPENT if ending else EXTENSION_SPENT_CONTINUE
    if not reason:
        return EXTENSION_REFUSAL
    return ""


def _grant(body: dict, reason: str, now: float | None) -> str:
    had = _budget_of(body, now)
    turns, deadline = _clamped_extension(body, had, now)
    gained_turns = turns - had.turns
    gained_seconds = deadline - had.deadline
    if gained_turns <= 0 and gained_seconds <= 0:
        return ""
    body["extensions_used"] = int(body.get("extensions_used") or 0) + 1
    body["turns_budget"] = turns
    body["deadline"] = deadline
    body["extension_reason"] = reason
    return (
        "Extended once by "
        + _granted_text(gained_turns, gained_seconds)
        + ": "
        + reason
    )


# Handle an extension request: grant it into *body*, or refuse it.
#
# Returns the notice to show. *body* is written only on a GRANT.
#
# REFUSE BY NAME (the no-gain return below, where ``_grant`` answers ""). The clamp
# left BOTH numbers where
# they were, so granting here would consume the session's
# one extension, change nothing, and REPORT a gain of 15
# turns and 10 minutes -- measured, and for EVERY charter
# naming a steps or minutes budget, because ``new_state``
# already clamped to that same figure. A success-shaped
# reply for work that did not happen is the worse failure:
# the model plans against turns it does not have and the
# run dies at the next budget check with nothing in the
# record explaining why. So nothing is written,
# ``extensions_used`` is NOT incremented,
# ``extension_reason`` is left unwritten, and the refusal
# names the ceiling that bound it. Mutant M6f deletes this
# branch; the upper-bound assertions in
# test_an_extension_cannot_outrun_the_charters_budget
# CANNOT see it, because an inequality is satisfied by the
# collapse -- the pin asserts this notice and
# ``extensions_used`` instead.
def _extension_notice(body: dict, reply: dict, now: float | None) -> str:
    """Handle an extension request: grant it into *body*, or refuse it."""
    reason = " ".join(str(reply.get("extension_reason") or "").split())[:400]
    # Computed BEFORE the notice text is chosen, and pure: nothing in
    # this block touches turn/turns_budget/deadline ahead of a GRANT,
    # so this is the exact value `status` gets at the return below.
    # See EXTENSION_SPENT_CONTINUE's comment -- the text must match it.
    ending = bool(stop_reason(body, now=now))
    refusal = _refusal_notice(body, reason, ending)
    if refusal:
        return refusal
    return _grant(body, reason, now) or (
        EXTENSION_NOT_GRANTED if ending else EXTENSION_NOT_GRANTED_CONTINUE
    )
