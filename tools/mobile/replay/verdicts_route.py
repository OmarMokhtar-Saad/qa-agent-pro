"""Replay engine: saved-route and actuation verdicts. Cut verbatim from tools/mobile/executor.py (P4.5)."""

from __future__ import annotations

from typing import Any

from tools.mobile.replay.elements import ACTUATING_OPS


def _route_expect_sentinel() -> str:
    return "<route-length-mismatch>"


def _route_expected(ctx: Any, index: int, total: int) -> str:
    """The screen_id step *index* of *total* must start on, or ``""`` when it is unchecked.

    Aligned FROM THE END: a budget-stop queue is prepended to a script by case_runner, and the
    saved route is always the tail. A route longer than the script can never match, so it
    returns a sentinel no screen_id equals.
    """
    expect = getattr(ctx, "route_expect", None) or ()
    offset = total - len(expect)
    if offset < 0:
        return _route_expect_sentinel()
    if index < offset:
        return ""
    return str(expect[index - offset] or "")


# Whether anything in THIS submit has already touched the device.
#
# The distinction the escape budget needs. A stop AFTER a tap or a type is a
# case that made progress and was handed back -- exactly what an escape is
# for. A stop BEFORE any of them, caused by one of our own selectors having
# gone stale, is bookkeeping, and ``case_runner`` declines to charge for it.
#
# Reads :data:`ACTUATING_OPS` rather than ``actions.MUTATING_OPS``, and the
# difference is one op that matters: ``wait`` is in MUTATING_OPS because a
# wait exists precisely to let the screen change, but it touches nothing. A
# script of ``[wait, tap(stale id)]`` was therefore charged an escape for a
# stop that actuated NOTHING -- a third of ``MAX_ESCAPES`` spent on the exact
# case ``MAX_FREE_STOPS`` was added to protect, while this docstring claimed
# the opposite.
#
# `list(trace)` RAISES on a non-iterable -- `_actuated(5)` was a TypeError,
# against this module's never-raise contract. The coercion is the same
# shape every other reader here uses: anything that is not a sequence of
# entries is no entries. Unreachable from today's callers, which pass the
# trace list they just built.
def _actuated(trace: Any) -> bool:
    """Whether anything in THIS submit has already touched the device."""
    entries = trace if isinstance(trace, (list, tuple)) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("outcome") or "") not in ("ok", "no_change", "unverified"):
            continue
        action = entry.get("action")
        action = action if isinstance(action, dict) else {}
        if str(action.get("op") or "") in ACTUATING_OPS:
            return True
    return False
