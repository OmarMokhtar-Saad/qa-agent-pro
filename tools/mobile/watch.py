"""Watched waits: what a wait says while it runs, and what ends it early.

A LEAF: it imports nothing from ``tools.mobile``, so the executor, the emulator
and the MCP server can all use it without an import cycle.

Two jobs, both about a wait the model cannot see into:

* PROGRESS. A long wait (an emulator boot, a screen that is loading) used to be
  silent, and the tester watched nothing for minutes. ``bind_progress`` hands
  this module the host's progress callback for the current tool call
  (``mcp_server._make_progress``), and a :class:`Ticker` sends one short line
  every :data:`PROGRESS_EVERY_S` seconds. Unbound (a unit test, a script) every
  call is a no-op.
* INTERRUPTIONS. ``classify_*`` name the thing that ended a wait early --
  ``"interrupted: <what>"`` -- from NEW facts only. A fact that was already true
  when the wait began (the app was already covered by a dialog) is the
  situation the caller started from, not an interruption.

No model runs here, and nothing sleeps: the caller owns the clock and the loop.
"""

from __future__ import annotations

import contextvars
import logging
import re  # noqa: F401
import time
from typing import Any

from config.settings_guard import never_raise
from tools.mobile.watch_classify import (  # noqa: F401
    _DISCONNECT_RE,
    _HTTP_RE,
    CALL_PACKAGES,
    ERROR_PHRASES,
    LOCK_PHRASES,
    MAX_QUOTE_CHARS,
    PREFIX,
    SHADE_PACKAGE,
    _quote,
    classify_error,
    classify_package,
    classify_texts,
)

logger = logging.getLogger(__name__)

#: Seconds between two progress lines from one wait. Progress is a notification
#: the host may show or drop; more often than this is chatter, not information.
PROGRESS_EVERY_S = 3.0

#: The longest progress line sent to the host.
MAX_PROGRESS_CHARS = 160

_REAL_MONOTONIC = time.monotonic

_progress: contextvars.ContextVar = contextvars.ContextVar(
    "qa_mobile_watch_progress", default=None
)


def bind_progress(callback: object) -> None:
    """Route this tool call's wait progress to *callback* (``async (str) -> None``).

    Bound per context, so one tool call's ticks never reach another call's host.
    """
    _progress.set(callback if callable(callback) else None)


def is_bound() -> bool:
    return _progress.get() is not None


async def say(message: str) -> None:
    """Send one progress line to the host. Never raises; a no-op when unbound."""
    callback = _progress.get()
    if callback is None:
        return
    with never_raise(logger, "mobile.watch: progress callback failed", exc_info=True):
        await callback(str(message)[:MAX_PROGRESS_CHARS])


class Ticker:
    """Emit ``<what> ... <n> s`` at most once per :data:`PROGRESS_EVERY_S`.

    Call :meth:`tick` once per poll; it decides whether a line is due. The clock
    is injectable and defaults to the real monotonic clock captured at import,
    so a test that swaps ``time.monotonic`` for a scripted list does not have
    its sequence consumed by progress bookkeeping.
    """

    def __init__(self, what: str, *, every: float | None = None, clock: Any = None):
        self._what = str(what)
        self._every = PROGRESS_EVERY_S if every is None else float(every)
        self._clock: Any = clock or _REAL_MONOTONIC
        self._start = self._clock()
        self._last = self._start

    async def tick(self, detail: str = "") -> bool:
        """True when a line was sent. Costs nothing while no host is bound."""
        if not is_bound():
            return False
        now = self._clock()
        if now - self._last < self._every:
            return False
        self._last = now
        line = self._what + " ... " + str(int(now - self._start)) + " s"
        if detail:
            line += " (" + str(detail) + ")"
        await say(line)
        return True
