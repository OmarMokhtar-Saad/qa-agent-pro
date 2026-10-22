"""Moved from handlers (refactor P2): _elicit."""

from __future__ import annotations

import logging
import time

from config.settings_guard import never_raise
from handlers._elicit_host import host
from handlers._elicit_orphan import _suppress_orphan_elicit_logs

logger = logging.getLogger("tools.mcp_handlers")


# How long the NEXT dialog on *cb* may wait, or None when the per-call budget
# is already spent and this is not the call's first dialog.
#
# The budget holder is attached to the callback object by
# ``mcp_server._make_elicitors`` -- a duck-typed private attribute rather than a
# parameter, so none of the ~15 handler signatures had to change. Both ends are
# cross-referenced: see ``_make_elicitors`` in mcp_server.py. A callback without
# one (tests, local matrix scripts) simply gets per-dialog bounding, which
# fails safe.
#
# The FIRST dialog of a call is floored at ELICIT_FLOOR_S even on an exhausted
# budget, so a dialog behind minutes of work is still asked. ``asked`` makes
# "first dialog" a recorded fact rather than something inferred from the clock.
#
# 2026-08-21: a dialog that TIMED OUT marks the shared holder ``dead``, and a
# dead transport skips every LATER dialog of the same call. The live TICKET-5645
# run spent 80.0s of an 80.02s tool call re-asking a transport that had already
# proven unresponsive: elicit_choice returned timed_out=True and NOTHING read
# it. Checked AFTER the first-dialog floor above, so that guarantee is
# unchanged, and per-CALL because the holder is per-call.
# Never raises.
def elicit_wait_s(cb) -> float | None:
    with never_raise(logger, "elicit budget read failed", exc_info=True):
        budget = getattr(cb, "_elicit_budget", None)
        if not isinstance(budget, dict):
            return host().elicit_timeout_s()
        remaining = float(budget.get("deadline") or 0.0) - time.monotonic()
        if not budget.get("asked"):
            return max(
                min(host().elicit_timeout_s(), remaining), host().elicit_floor_s()
            )
        if budget.get("dead"):
            return None
        if remaining <= 0:
            return None
        return min(host().elicit_timeout_s(), remaining)
    return host().elicit_timeout_s()


def _elicit_dead(cb) -> bool:
    """True when an EARLIER dialog of this same call timed out.

    Read only for WORDING, by _log_elicit_skip. The skip DECISION stays in
    elicit_wait_s, which remains the single owner of it. Never raises."""
    with never_raise(logger, "elicit dead read failed", exc_info=True):
        budget = getattr(cb, "_elicit_budget", None)
        return bool(isinstance(budget, dict) and budget.get("dead"))
    return False


# A2 (2026-08-21, TICKET-5138): both helpers logged "per-call elicitation budget
# spent" for every skip. On the live run the choice dialog timed out at
# 21:06:12,506 and its own elicit_text fallback was skipped 2 ms later -- 2 ms
# into an 80s budget, so nothing was spent; the cause was the dead-mark. A
# timed-out prompt must not be ACCOUNTED as its own fallback's budget spend.
#
# The fallback is still skipped, deliberately: a transport that just proved it
# does not answer within the bound renders the second dialog just as invisibly,
# so re-asking buys another blind wait and no answer. What reaches the tester is
# the markdown menu the call site already relays. Never raises.
def _log_elicit_skip(what: str, cb, message: str) -> None:
    """Log a SKIPPED dialog with its REAL cause."""
    if _elicit_dead(cb):
        logger.warning(
            "mcp %s skipped for %r -- an earlier dialog in this call timed out, "
            "so this client is not answering (per-call budget NOT spent)",
            what,
            message,
        )
    else:
        logger.warning(
            "mcp %s skipped for %r -- per-call elicitation budget spent",
            what,
            message,
        )


def _mark_elicit_asked(cb) -> None:
    """Record that this call has now shown a dialog, so the floor applies once."""
    with never_raise(logger, "elicit budget mark failed", exc_info=True):
        budget = getattr(cb, "_elicit_budget", None)
        if isinstance(budget, dict):
            budget["asked"] = True


def _mark_elicit_dead(cb) -> None:
    """Record that a dialog on *cb* TIMED OUT, so later dialogs of the SAME call
    are skipped instead of re-waiting on a transport that has proven unresponsive.

    ONLY a timeout sets this. A DECLINED dialog means the tester saw the question
    and dismissed it, and an answered one means the transport works -- both must
    leave the next dialog askable. Same never-raising discipline as
    _mark_elicit_asked, and the same per-call holder, so deadness never leaks
    across tool calls."""
    # A3 (2026-08-21): abandoning a dialog is exactly when a late answer becomes
    # EXPECTED, so this is where the orphan-response filter is armed. One site
    # covers both helpers -- every timeout branch already calls this function.
    _suppress_orphan_elicit_logs()
    with never_raise(logger, "elicit budget dead-mark failed", exc_info=True):
        budget = getattr(cb, "_elicit_budget", None)
        if isinstance(budget, dict):
            budget["dead"] = True
