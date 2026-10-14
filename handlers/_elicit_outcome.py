"""Moved from handlers (refactor P2): _elicit."""

from __future__ import annotations

import logging
import time

from config.settings_guard import never_raise
from handlers import _common
from handlers._elicit_budget import _log_elicit_skip, _mark_elicit_dead
from handlers._elicit_gate import note_elicit_answered
from handlers._elicit_host import host
from handlers._elicit_orphan import _rearm_orphan_elicit_logs

logger = logging.getLogger("tools.mcp_handlers")


# ONLY a CHOSEN or DECLINED result counts as evidence that the tester saw the
# dialog. UNAVAILABLE does not: it is what a raising ctx.elicit degrades to,
# which says nothing about the human, and clearing strikes on it would make a
# client whose transport raises every time permanently ungatable.
#
# 2026-09-01: with ONE exception, and it is the reason the gate never closed
# on a headless client. A DECLINED that came back in under
# ``_INSTANT_DECLINE_S`` is a strike, not an answer -- nobody read it. A
# CHOSEN always clears, however fast: a value came back, so something
# answered. *elapsed_s* is None from callers that did not time the round
# trip, and those keep the old behaviour exactly.
# Never raises.
#
# A3 re-arm (2026-08-21). The status carve-out is NOT widened: it is
# load-bearing and separately pinned -- an UNAVAILABLE result is a
# raising transport, not an answer. A live transport means a later
# unknown-request-ID record is unexplained again, so the demotion
# filter is removed here.
def _note_elicit_outcome(result, elapsed_s=None) -> None:
    """Feed one completed dialog result into the per-client record."""
    with never_raise(logger, "elicit outcome note failed", exc_info=True):
        _status = getattr(result, "status", None)
        if _is_instant_decline(_status, elapsed_s):
            logger.debug(
                "mcp elicit declined in %.2fs -- too fast to have been read; "
                "counting a strike rather than an answer",
                elapsed_s,
            )
            host().note_elicit_timeout()
            return
        if _status in (_common.CHOSEN, _common.DECLINED):
            note_elicit_answered()
            _rearm_orphan_elicit_logs()


def _is_instant_decline(status, elapsed_s) -> bool:
    """True for a DECLINED that came back faster than a human could read it."""
    return bool(
        status == _common.DECLINED
        and elapsed_s is not None
        and elapsed_s < host().instant_decline_s()
    )


def _skipped(what: str, cb, message: str) -> _common.ChoiceResult:
    """The dialog was never asked: log the real cause, report a budget skip."""
    _log_elicit_skip(what, cb, message)
    return _common.ChoiceResult(_common.UNAVAILABLE, budget_skipped=True)


# ONLY the timeout branch calls this. A proven-dead transport must not cost the
# rest of this call another full dialog bound. The bare `except Exception`, the
# DECLINED result and the answered result all leave the next dialog askable -- a
# tester who dismisses dialog 1 must still get dialog 2.
def _after_timeout(cb) -> _common.ChoiceResult:
    """Mark the transport dead, strike the client, report a timed-out result."""
    _mark_elicit_dead(cb)
    # ...and one strike against the CLIENT, which outlives this call. Two in a
    # row with no answer between them gate it for the process.
    host().note_elicit_timeout()
    return _common.ChoiceResult(_common.UNAVAILABLE, timed_out=True)


def _settled(result, started: float) -> _common.ChoiceResult:
    """Feed a real ChoiceResult to the per-client record; anything else is UNAVAILABLE."""
    if isinstance(result, _common.ChoiceResult):
        _note_elicit_outcome(result, time.monotonic() - started)
        return result
    return _common.ChoiceResult(_common.UNAVAILABLE)
