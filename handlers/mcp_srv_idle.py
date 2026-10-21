"""Idle-out: a server nobody uses exits, and its launcher respawns it on demand.

Each MCP client starts its own launcher and stdio server; a client that only
ever sent ``initialize`` would otherwise keep a server alive for the whole
editor session. The decision lives HERE, next to the in-flight and live-run
accounting the drift restart already trusts, and is taken under the same lock
a tool call enters through, so a request can never slip in between the check
and the exit. Only a launcher opts in (``MCP_LAUNCHER_IDLE_OK``)."""

from __future__ import annotations

import logging
import os
import time

from config.settings_guard import never_raise
from handlers.mcp_srv_state import _INFLIGHT, _INFLIGHT_LOCK, BUSY_PROBES

logger = logging.getLogger(__name__)

#: Exit code the launcher recognises (LAUNCHER_TEMPLATE.IDLE_EXIT_CODE).
IDLE_EXIT_CODE = 87
#: Quiet time (no tool running, no live run, none finished) before idling out.
IDLE_EXIT_S = 600.0
#: Set by the launcher that owns this server's stdio.
IDLE_ENV = "MCP_LAUNCHER_IDLE_OK"

STARTED = time.monotonic()
#: Seam for tests: the real exit skips cleanup on purpose (a tool never runs here).
exit_process = os._exit
#: A run ending restarts the quiet clock (stamped when the end is noticed).
_RUN = {"seen": 0.0, "busy": False}


def _runs_fail_closed() -> int:
    """Live runs; a probe that errors counts as busy (never idle out blind)."""
    with never_raise(logger, "idle run check failed", exc_info=True):
        return sum(int(probe()) for probe in list(BUSY_PROBES))
    return 1


def idle_exit_enabled() -> bool:
    return os.environ.get(IDLE_ENV) == "1"


def seconds_until_idle() -> float:
    """Exit now when idle long enough; else how long until it could be."""
    with _INFLIGHT_LOCK:
        if _INFLIGHT["n"] or _runs_fail_closed():
            _RUN["busy"] = True
            return IDLE_EXIT_S
        if _RUN["busy"]:
            _RUN.update(busy=False, seen=time.monotonic())
        quiet = time.monotonic() - max(_INFLIGHT["last_finish"], STARTED, _RUN["seen"])
        if quiet >= IDLE_EXIT_S:
            exit_process(IDLE_EXIT_CODE)
    return IDLE_EXIT_S - quiet


def idle_watch() -> None:
    """Sleep to the deadline, re-check, repeat (a deadline wait, not a poll)."""
    if not idle_exit_enabled():
        return
    while True:
        time.sleep(seconds_until_idle())
