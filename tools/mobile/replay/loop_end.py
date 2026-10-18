"""Replay engine: the reply of a script that ends without done(). Cut verbatim from tools/mobile/replay/replay.py (C5 w17)."""

from __future__ import annotations

from tools.mobile.replay.context import (
    STATUS_DONE,
    STATUS_UNVERIFIED,
    ReplayStop,
    _ReplayRun,
    _result,
)
from tools.mobile.replay.guard import NOTHING_MOVED_NOTE, inert_ops
from tools.mobile.replay.screen import _has_verification, _verification_summary
from tools.mobile.replay.steps_stop import _reply

_REASON_VERIFIED_TAIL = (
    ". The script ended without a done() action, so this verdict "
    "rests on the asserts above."
)
_REASON_UNASSERTED = (
    "The script finished without a done() action. Nothing in it "
    "asserted anything, so there is no evidence this case passed "
    "-- a screen that moved is activity, not verification."
)
_REASON_ABSENCE_ONLY = (
    "No verdict for this turn: it ended without a done() action and its only "
    "passed asserts were absence checks (%s), which a blank or wrong screen "
    "also satisfies. Add an assert that something expected IS on screen, then "
    "call done() with a verdict."
)
_REASON_EXPLORATORY = (
    "No verdict for this turn: it ended without a done() action and asserted "
    "nothing, which is ordinary for an exploratory turn -- it only recorded "
    "what the screen did. The run is NOT finished: to verify the goal, add "
    "assert actions on what you expect, then call done() with a verdict."
)


# The reason a script that never called done() ends with.
#
# A VERIFIED run must say what it verified. The reason used to open "The
# script finished without a done() action" on BOTH exits, so a tester
# reading a pass was told about an omission in the script rather than about
# the app. The omission is still disclosed -- second sentence -- because it
# is why there is no model-written reason here.
# FOUR branches, because two questions are being answered: was anything
# verified, and -- if not -- is that an omission in THIS unit of work?
#
# The verified branch is split. ``_verification_summary`` describes THIS
# trace, so when the evidence came from an earlier turn of the run there is
# nothing for it to summarise, and the old fallback ("the asserts in this
# script passed") would make a per-script claim about run-wide evidence --
# inaccurate on exactly the path ``_verified`` opens here.
def _no_done_reason(ended_verified: bool, summary: str, asserts_expected: bool) -> str:
    """The reason a script that never called done() ends with (why: above)."""
    if ended_verified and summary:
        return "Verified: " + summary + _REASON_VERIFIED_TAIL
    if summary:  # asserts passed, but none was a positive check
        return _REASON_ABSENCE_ONLY % summary[:200]
    if asserts_expected:
        return _REASON_UNASSERTED
    return _REASON_EXPLORATORY


# A script that never calls done() lands here. It used to hand back an
# empty verdict, which `case_runner` reads as PASS -- so omitting one word
# bypassed the whole verification rule the done() branch enforces. The same
# rule applies on both exits or it is not a rule.
#
# ``_has_verification``, NOT the run-wide ``_verified``: an assert an earlier
# turn made covered a different field or screen, so a turn that asserted nothing
# itself carries no verdict -- it is rendered "No verdict", never inherits one.
# (done() still judges the goal run-wide; only this exit is per-script.)
#
# THE TRACE THIS REASON IS SUMMARISING, actually read. Every branch above
# is derived from `ended_verified` and `ctx.asserts_expected` alone, so all
# four claimed the script did something -- "it recorded what the screen
# did" -- on a script where every action came back `no_change`. Appended
# rather than folded into the ladder because it is orthogonal to all four:
# a verified turn and an exploratory one are equally worth telling that
# nothing moved.
def _end_of_script(run: _ReplayRun, screen: dict | None) -> dict:
    """The reply of a script that never calls done() (why: above)."""
    ctx, trace = run.ctx, run.trace
    ended_verified = _has_verification(trace)
    summary = _verification_summary(trace)
    reason = _no_done_reason(ended_verified, summary, ctx.asserts_expected)
    inert = inert_ops(trace)
    if inert:
        reason = (NOTHING_MOVED_NOTE % ", ".join(inert)) + reason
    verdict = "" if ended_verified else STATUS_UNVERIFIED
    stop = ReplayStop(STATUS_DONE, trace, screen, verdict, reason, len(run.items) - 1)
    return _reply(_result(stop))
