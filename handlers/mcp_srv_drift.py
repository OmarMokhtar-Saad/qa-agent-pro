"""Drift-restart helpers: in-flight accounting, the idle exit and its logging."""

from __future__ import annotations

import logging
import os
import random
import time

from config.settings_guard import never_raise
from handlers.mcp_srv_state import (
    _DEFER_WARN_EVERY,
    _DRAIN_IDLE_S,
    _DRIFT_EXIT_JITTER_S,
    _INFLIGHT,
    _INFLIGHT_LOCK,
    _RUN_DEFER_MAX_S,
    BUSY_PROBES,
    DRIFT_RESTART_EXIT_CODE,
    logger,
)
from tools.process_env import get_env


def _drift_restart_enabled() -> bool:
    """Kill-switch for the drift restart. An ENV read, not a settings field, so
    an operator can disable it without the restart that settings would need."""
    raw = str(get_env("QA_DRIFT_RESTART_ENABLED", "true")).strip().lower()
    return raw not in ("0", "false", "no", "off")


def _inflight_enter() -> None:
    with _INFLIGHT_LOCK:
        _INFLIGHT["n"] += 1


def _inflight_exit() -> None:
    with _INFLIGHT_LOCK:
        _INFLIGHT["n"] = max(0, _INFLIGHT["n"] - 1)
        _INFLIGHT["last_finish"] = time.monotonic()


def _drift_interval() -> float:
    """Seconds between drift ticks (QA_DRIFT_CHECK_SECONDS, floor 5, default 30)."""
    try:
        return max(5.0, float(get_env("QA_DRIFT_CHECK_SECONDS", "30")))
    except (TypeError, ValueError, OverflowError):
        return 30.0


def _log_drift_blocked(blocked: int, mismatched) -> None:
    """Log a tick whose tree does not verify; escalate every Nth one."""
    if blocked % _DEFER_WARN_EVERY == 0:
        logger.warning(
            "drift: blocked for %d checks — %d file(s) still do not "
            "match the manifest (%s). This is no longer a transient "
            "update window; the new version will not be loaded.",
            blocked,
            len(mismatched),
            ", ".join(sorted(mismatched)[:5]),
        )
    else:
        logger.info(
            "drift: a new version is on disk but the tree does not "
            "verify yet — waiting for the update to finish."
        )


def _drift_jitter_sleep() -> None:
    """Sleep a random 0..jitter OUTSIDE the lock (a blocking sleep under it
    would stall every _inflight_enter, i.e. every tool call). Its whole job is
    to de-synchronise the peer clients that share this install and detect the
    same update on the same tick."""
    with never_raise(logger, "drift exit jitter failed", exc_info=True):
        _jitter = random.uniform(0.0, _DRIFT_EXIT_JITTER_S)
        # Guarded so a zero jitter never calls sleep: the drift-watch
        # tests pin random.uniform to 0.0 and count sleep ticks.
        if _jitter > 0:
            time.sleep(_jitter)


def _probed_runs() -> int:
    """Sum of the registered busy probes (live mobile runs); 0 on any failure.

    A run sits idle BETWEEN step calls (the host is thinking), longer than the
    drain gap, so in-flight accounting alone would restart under it and drop
    its lease."""
    with never_raise(logger, "active-run check failed", exc_info=True):
        return sum(int(probe()) for probe in list(BUSY_PROBES))
    return 0


def _mobile_runs_active() -> int:
    """Live runs that defer the restart, for at most _RUN_DEFER_MAX_S in a row.

    Bounded because a run waiting on the tester is re-armed by its idle timer
    forever. LAUNCHER restarts (integrity heal, .env change) cannot see these
    runs at all: the launcher only watches stdio idleness, and a marker file
    would need a stale-writer protocol, so that path is left as documented."""
    runs = _probed_runs()
    if not runs:
        _INFLIGHT.pop("runs_since", None)
        return 0
    since = _INFLIGHT.setdefault("runs_since", time.monotonic())
    return 0 if time.monotonic() - since > _RUN_DEFER_MAX_S else runs


def _exit_if_idle() -> tuple:
    """Exit the process when no tool runs and the quiet gap has passed; else
    return (busy, idle). The exit happens UNDER the lock _inflight_enter also
    takes, so a tool cannot slip in between the check and the exit."""
    with _INFLIGHT_LOCK:
        busy = _INFLIGHT["n"] + _mobile_runs_active()
        idle = time.monotonic() - _INFLIGHT["last_finish"]
        if not busy and idle >= _DRAIN_IDLE_S:
            logging.shutdown()  # os._exit skips atexit: flush log files
            os._exit(DRIFT_RESTART_EXIT_CODE)
    return busy, idle


def _log_drift_deferred(deferrals: int, busy, idle) -> None:
    if deferrals % _DEFER_WARN_EVERY == 0:
        logger.warning(
            "drift: restart deferred %d times — this install is not "
            "picking up releases. Restart the editor to apply it.",
            deferrals,
        )
    else:
        logger.info(
            "drift: restart deferred (%d busy: tools or runs, %.0fs since the last "
            "tool finished, deferral #%d).",
            busy,
            idle,
            deferrals,
        )
