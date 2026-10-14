"""The child-side drift watch: replace this process when the install changes."""

from __future__ import annotations

import time
from pathlib import Path

import tools.updater as updater
from config.settings_guard import never_raise
from handlers import _common, export, setup_reload_gate
from handlers.mcp_srv_drift import (
    _drift_interval,
    _drift_jitter_sleep,
    _drift_restart_enabled,
    _exit_if_idle,
    _log_drift_blocked,
    _log_drift_deferred,
)
from handlers.mcp_srv_state import logger


# Dist only: replace this process when the install it runs from changes.
#
# WHY (F1): three MCP clients (Claude Desktop, Cursor, Claude Code) launch the
# SAME install dir. The launcher's watchdog restarts its child only when IT won
# the update race, so a peer client's update left this process serving stale
# code indefinitely -- observed 2026-07-29, a launcher still emitting the
# pre-7265baa "set read-only" wording while VERSION on disk read 1.10.7. The
# check lives in the CHILD, not the launcher, because only the child can see
# whether a tool is running.
#
# Scope of the guarantee: no TOOL BODY is executing. FastMCP's result
# serialization and non-tool traffic (tools/list, ping) are outside it, and the
# quiet gap is what covers them in practice.
#
# Never raises: every failure path just skips the tick, so this can only ever
# MISS a restart, never cause a spurious one.
# 2026-08-04: the drift TICK no longer shares QA_UPDATE_INTERVAL_MINUTES'
# 15-minute clock. This loop's steady-state cost is reading the local
# VERSION/pyproject (no network; the manifest verify below runs only AFTER
# a change is detected), so a fast tick is essentially free -- while it
# rode the network cadence, a peer-applied release took up to 15 minutes
# to reach the other clients' servers (v1.39.0 rollout: applied 09:04:30,
# the two stale Cursor servers restarted only at their 09:07 marks). The
# NETWORK check keeps its own 15-minute clock in the launcher's watchdog.
def _drift_watch() -> None:
    """Dist only: replace this process when the install it runs from changes."""
    if not (
        _common.test_cases_only()
        and setup_reload_gate._DIST_UPDATE_REPO
        and _drift_restart_enabled()
    ):
        return
    _drift_loop(
        _drift_interval(),
        export.code_changed_since_start,
        lambda: updater.verify_integrity(Path(updater._INSTALL_DIR)),
    )


def _idle_gate(deferrals: int) -> tuple:
    # Logged BEFORE the lock: os._exit while holding it is safe (the
    # process dies), but a BLOCKING stderr write while holding it would
    # stall every _inflight_enter, i.e. every tool call.
    logger.info(
        "drift: installed version changed since this process loaded — "
        "restarting as soon as no tool is running."
    )
    # Jitter ONCE -- on the first tick that sees this drift.
    if deferrals == 0:
        _drift_jitter_sleep()
    busy, idle = _exit_if_idle()
    return busy, idle


# apply_update overlays file-by-file in sorted order, so pyproject.toml/VERSION
# can land BEFORE tools/. Exiting onto a half-written tree would be worse than
# the staleness this fixes, so wait until the tree verifies against its own
# manifest. The blocked branch escalates: a PERSISTENT mismatch (a locally
# edited file, a partial update) would otherwise disable the restart for the
# life of the process while logging a reassuring "waiting" line forever.
# Never raises; a failed tick is skipped.
def _drift_loop(interval: float, code_changed, verify_tree) -> None:
    """Tick forever: on a verified drift, exit once no tool is running."""
    deferrals = 0
    blocked = 0
    while True:
        time.sleep(interval)
        with never_raise(logger, "drift check failed", exc_info=True):
            if not code_changed():
                continue
            mismatched = verify_tree()
            if mismatched:
                blocked += 1
                _log_drift_blocked(blocked, mismatched)
                continue
            blocked = 0
            busy, idle = _idle_gate(deferrals)
            deferrals += 1
            _log_drift_deferred(deferrals, busy, idle)
