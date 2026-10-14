"""What a guard hit does, the budget clamps, the depth line and the off-charter log."""

from __future__ import annotations

from typing import Any

from tools.mobile.charter_coerce import _choice, _int
from tools.mobile.charter_consts import DEPTH_DIRECTIVES, MAX_OFF_CHARTER_RECORDS
from tools.mobile.charter_schema import DEPTHS, DESTRUCTIVE, PAUSE, REFUSE


# ``destructive`` -> :data:`PAUSE` or :data:`REFUSE`. The WHOLE decision.
#
# ``none`` asks to REFUSE: the tester said this run changes nothing, so a
# guard hit should end the attempt rather than open a conversation.
# ``reversible`` and ``allowed`` ask to PAUSE, which is what the lane does
# today.
#
# **The refuse IS implemented, since step 5.** ``session._submit_explore``
# reads this function for the run's own charter and sets
# ``executor.Context.guard_refuses``; at the one guard-hit site a refusing
# run gets ``executor.GUARD_DETAIL_REFUSED`` and a TERMINAL status instead
# of the tester pause, and ``session`` records the stop -- so neither the
# reply nor the report can imply the run continued.
#
# What this function does NOT do, stated because a reader will assume it
# does: it does not widen the destructive lexicon, it does not decide
# WHETHER an action is stopped -- the guard alone does that, unchanged --
# and no value it returns lets an action through. It decides only what
# happens AFTER a hit.
def guard_policy(destructive: object) -> str:
    """``destructive`` -> PAUSE or REFUSE. The WHOLE decision."""
    return REFUSE if _choice(destructive, DESTRUCTIVE, "none") == "none" else PAUSE


def budget_cap(charter: object) -> tuple:
    """``(steps, seconds)`` the tester ASKED for; ``0`` where they asked for none.

    The ONE producer of the requested numbers, read by both the initial clamp
    (:func:`budget_for`) and the EXTENSION clamp in
    ``explore_runner.apply_turn_result``. Two derivations of "what did the
    charter ask for" would drift, and the extension site is exactly where the
    first one was missing.
    """
    body = charter if isinstance(charter, dict) else {}
    budget = body.get("budget")
    budget = budget if isinstance(budget, dict) else {}
    steps = max(0, _int(budget.get("steps"), 0))
    minutes = max(0, _int(budget.get("minutes"), 0))
    return steps, minutes * 60


def budget_for(charter: object, max_steps: int, max_seconds: int) -> tuple:
    """``(steps, seconds)`` for this charter, clamped DOWNWARD only.

    A charter can shorten a run and can never lengthen one: the lane's own
    ceilings stay the ceilings, and a tester asking for ten thousand steps gets
    the lane's maximum. Zero -- the default -- means "the lane's own budget".

    This is the START of a run. A run can also be EXTENDED mid-flight, which is
    a SECOND writer of the same two numbers; it re-clamps through
    :func:`budget_cap` at its own site, because this function is not on that
    path and cannot bound it.
    """
    steps, seconds = budget_cap(charter)
    out_steps = max(1, min(steps, int(max_steps))) if steps > 0 else int(max_steps)
    out_seconds = (
        max(60, min(seconds, int(max_seconds))) if seconds > 0 else int(max_seconds)
    )
    return out_steps, out_seconds


def depth_directive(charter: object) -> str:
    """What this run's ``depth`` asks the model to ATTEMPT. ONE producer.

    The turn packet carries this sentence and nothing else in this server
    derives it. NOT a tactics catalogue -- see :data:`DEPTH_DIRECTIVES`.
    """
    body = charter if isinstance(charter, dict) else {}
    return DEPTH_DIRECTIVES[_choice(body.get("depth"), DEPTHS, "both")]


# Append ONE off-charter visit -- DEDUPLICATED and BOUNDED. ONE producer.
#
# **The identity rule: one record per ``(screen_id, matched)`` pair.** What
# the tester needs to know is that the run TOUCHED an excluded flow and
# which line matched it; how many consecutive turns the model lingered there
# is a property of the model's next move, not of the charter, and recording
# it fills the manifest with copies of one fact. Measured: without this the
# same screen was recorded on turn 1 and again on turn 2.
#
# This is also the ONE reader of :data:`MAX_OFF_CHARTER_RECORDS` -- the cap
# is applied HERE rather than at the call site, so the bound and the
# identity rule cannot be applied in one place and forgotten in another.
def record_off_charter(visits: Any, entry: object) -> list:
    """Append ONE off-charter visit -- DEDUPLICATED and BOUNDED. ONE producer."""
    out = [dict(item) for item in list(visits or []) if isinstance(item, dict)]
    body = entry if isinstance(entry, dict) else {}
    key = (str(body.get("screen_id") or ""), str(body.get("matched") or ""))
    seen = {
        (str(item.get("screen_id") or ""), str(item.get("matched") or ""))
        for item in out
    }
    if key not in seen:
        out.append(dict(body))
    return out[-MAX_OFF_CHARTER_RECORDS:]
