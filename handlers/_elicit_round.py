"""Moved from handlers (refactor P2): _elicit."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from config.settings_guard import never_raise
from handlers import _common
from handlers._elicit_budget import _mark_elicit_asked
from handlers._elicit_host import host
from handlers._elicit_opts import AskCb
from handlers._elicit_outcome import _after_timeout, _settled, _skipped

logger = logging.getLogger("tools.mcp_handlers")


def _open(cb: Any, what: str, message: str) -> Any:
    """``(cb, wait_s, started)`` once the dialog is marked asked, else the result to return now."""
    if cb is None:
        return _common.ChoiceResult(_common.UNAVAILABLE)
    wait_s = host().elicit_wait_s(cb)
    if wait_s is None:
        return _skipped(what, cb, message)
    _mark_elicit_asked(cb)
    return cb, wait_s, time.monotonic()


_FAILED = "mcp %s failed for %r"
_TAIL = "; falling back to the text menu"


def _unavailable() -> _common.ChoiceResult:
    """A transport error (already logged at DEBUG): degrade to UNAVAILABLE."""
    return _common.ChoiceResult(_common.UNAVAILABLE)


def _timed_out(cb: Any, *log_args: object) -> _common.ChoiceResult:
    """WARNING for an unanswered dialog, then the timeout fallback."""
    logger.warning("mcp %s timed out after %.0fs for %r%s", *log_args)
    return _after_timeout(cb)


# Run one elicitation round, degrading to UNAVAILABLE on a missing callback
# or any transport error (e.g. a client without elicitation support).
#
# 2026-08-10 (K1): also bounded -- by ELICIT_TIMEOUT_S per dialog and by the
# per-call budget. An unanswered dialog used to hold the whole tool call until the
# client killed it at ~120s.
#
# A2: a skipped dialog's cause is logged by _log_elicit_skip, which
# distinguishes a spent budget from a transport this call already proved
# unresponsive.
# The timeout branch comes BEFORE the bare `except Exception` --
# asyncio.TimeoutError is an Exception subclass on every version this project
# supports (>=3.10), so the ordering is load-bearing. WARNING, not DEBUG: the
# installed log runs at INFO and a silent timeout is undiagnosable from the
# log file. The warning says WHAT HAPPENS NEXT (fix round 3, item 6): the
# caller falls back to its text menu, and this log is where that is diagnosed.
async def elicit_choice(
    choose: _common.ChooseCb, message: str, options: list
) -> _common.ChoiceResult:
    opened = _open(choose, "elicit_choice", message)
    if isinstance(opened, _common.ChoiceResult):
        return opened
    ask, wait_s, started = opened
    with never_raise(logger, _FAILED, "elicit_choice", message, exc_info=True) as g:
        try:
            result = await asyncio.wait_for(ask(message, list(options)), timeout=wait_s)
        except asyncio.TimeoutError:
            return _timed_out(choose, "elicit_choice", wait_s, message, _TAIL)
    return _unavailable() if g.failed else _settled(result, started)


# One free-text elicitation round; UNAVAILABLE without a callback or on
# any transport error (mirrors elicit_choice), and bounded the same way -- see
# elicit_wait_s and mcp_server._make_elicitors.
#
# A2 and the timeout branch: see the sibling in elicit_choice.
async def _elicit_text(ask_text: AskCb, message: str) -> _common.ChoiceResult:
    opened = _open(ask_text, "elicit_text", message)
    if isinstance(opened, _common.ChoiceResult):
        return opened
    ask, wait_s, started = opened
    with never_raise(logger, _FAILED, "elicit_text", message, exc_info=True) as g:
        try:
            result = await asyncio.wait_for(ask(message), timeout=wait_s)
        except asyncio.TimeoutError:
            return _timed_out(ask_text, "elicit_text", wait_s, message, "")
    return _unavailable() if g.failed else _settled(result, started)
