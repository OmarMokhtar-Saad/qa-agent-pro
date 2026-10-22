"""The reload gate of qa-doctor and the dist update repo (moved from setup_check)."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time

from handlers import (
    _common,
    export,
    setup_reload_marker,
    setup_reload_report,
    setup_update_idle,
)
from handlers.mcp_srv_state import _INFLIGHT, _INFLIGHT_LOCK
from tools import updater, updater_peek

# Baked by scripts/build_dist.py in the public distribution ("owner/repo").
# Empty in the private checkout, which disables the on-demand update path.
_DIST_UPDATE_REPO = "OmarMokhtar-Saad/qa-agent-pro"

# The headline qa-doctor shows for each reload reason, keyed by the reason
# schedule_reload records in the marker.
RELOAD_HEADLINES: dict[str, str] = {
    "update": (
        "🔄 **A new version was just installed.** The server is "
        "reloading now to apply it."
    ),
    "code": (
        "🔄 **Newer code is installed than this server is "
        "running.** Another process updated this install while "
        "this one was already started, so it is serving stale "
        "modules. Reloading now."
    ),
    "config": (
        "🔄 **Configuration changed.** `.env` was edited after "
        "this server started, so the settings this process is "
        "using are the ones it booted with — not what the file "
        "says now. Reloading to apply them."
    ),
}


def reloading_message(headline: str) -> str:
    """The ONLY thing a setup check that just scheduled a reload may say.

    Everything the full report renders -- backend, Jira, feature gates -- comes
    from THIS process's in-memory config, which is seconds from being thrown
    away. Splicing a note into that report (what this used to do) still showed
    the tester a complete-looking "✅ Ready" verdict over values that could
    already be wrong, so say only what is certainly true and stop."""
    return (
        "## Setup check\n\n"
        f"{headline} This takes about 10 seconds — run `qa-doctor` again "
        "to see the current status, including whether the reload worked.\n\n"
        "_Environment, backend, Jira and feature-gate details are deliberately "
        "not shown here: this process is being replaced, so anything it "
        "reported could already be out of date._"
    )


#: Longest the reload waits for in-flight tool calls (the doctor's own reply included).
DRAIN_TIMEOUT_S = 30.0
#: Seconds after the last call ends for its reply to flush before the exit.
FLUSH_GRACE_S = 2.0
_DRAIN_POLL_S = 0.1


def _calls_in_flight() -> int:
    with _INFLIGHT_LOCK:
        return int(_INFLIGHT["n"])


def wait_until_drained(timeout: float = DRAIN_TIMEOUT_S) -> bool:
    """Block until no tool call is in flight (the reply that scheduled this
    reload has been returned), or *timeout* seconds; ``True`` when drained.
    Exiting earlier lost that reply and the client waited 120 s (-32001)."""
    deadline = time.monotonic() + timeout
    while _calls_in_flight() and time.monotonic() < deadline:
        time.sleep(_DRAIN_POLL_S)
    return not _calls_in_flight()


def _flush_std_streams() -> None:
    """Best-effort flush of plain stdout/stderr before the exit. The MCP stdio
    writer keeps its own buffer this cannot reach: that reply is covered by the
    ``FLUSH_GRACE_S`` grace slept before this call."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError, AttributeError):
            pass
    # os._exit skips atexit, so flush the log file handlers here. The telemetry
    # (posthog) atexit flush is deliberately NOT run: it can block on the network.
    logging.shutdown()


def _exit_after_reply() -> None:
    """Wait for the reply of the call that scheduled the reload, then exit."""
    wait_until_drained()
    time.sleep(FLUSH_GRACE_S)
    _flush_std_streams()
    os._exit(86)


def schedule_reload(reason: str = "") -> None:
    """Exit the server process shortly after the current response flushes.

    Distribution installs only, right after an on-demand update: the
    supervising launcher (start.sh) respawns the server on the NEW code and
    replays the MCP handshake, so the editor session never notices.

    *reason* is "update", "code" or "config" and is recorded in the reload
    marker so the successor can judge the outcome: an update reload must come
    back on a new version, while a config reload is expected to keep the same
    one. The marker is written HERE, before the thread starts -- so it is always
    on disk before the exit, and EVERY caller gets the verification for free."""
    setup_reload_marker._write_reload_marker(reason)

    threading.Thread(target=_exit_after_reply, daemon=True).start()


def pending_reload_reason(update_status: str) -> str | None:
    """Why this process must reload now, or None; the first match wins.

    "code" (ops-6, bug 2): the trigger once watched ONLY .env, so an update
    applied by ANOTHER process sharing this install dir left this one serving
    stale modules with no signal -- run_update_check already saw the new disk
    and said "up-to-date" (2026-07-29: 1.10.4 on disk while a process from
    08:57 kept serving 1.10.3, so one host-mode flow ran prepare and submit on
    different code). "config": the settings the report renders came from the
    OLD .env, so they must not be shown as the applied config."""
    if update_status in ("updated", "healed"):
        return "update"
    if export.code_changed_since_start():
        return "code"
    if export.env_changed_since_start():
        return "config"
    return None


async def update_status_for(repo: str) -> str:
    """Install the update, or only peek for one while a host session is live."""
    if setup_update_idle.session_busy():
        # Installing would exit this server under the live session (G07).
        return await asyncio.to_thread(updater_peek.peek_update, repo)
    return await asyncio.to_thread(
        updater.run_update_check,
        force=True,
        repo_override=repo,
        lock_override=True,
    )


# Runs only on the dist edition with an update repo; otherwise ("", None).
# ops-8: a scheduled reload ends this process within seconds, so the reply
# is ONLY the honest headline -- see reloading_message for why splicing a
# note into the full report misled testers.
async def run_reload_gate(progress: _common.ProgressCb) -> tuple[str, str | None]:
    """qa-doctor's update check: (update_status, reply when a reload is due)."""
    if not (_common.test_cases_only() and _DIST_UPDATE_REPO):
        return "", None

    await _common.emit(progress, "⬆️ Checking for the latest release…")
    update_status = await update_status_for(_DIST_UPDATE_REPO)
    reason = pending_reload_reason(update_status)
    if reason is None:
        return update_status, None
    schedule_reload(reason)
    return update_status, reloading_message(RELOAD_HEADLINES[reason])


# A reload that did not take effect is not passive information: the
# server may be serving stale code, so it belongs in the action items.
# Recommended, not blocking -- the server still answers.
# The same hazard on an install that cannot reload itself. Guarded by
# the NEGATION of the reload gate above, deliberately: where the reload
# path runs it has already returned a `reloading_message`, so reaching
# here at all means no reload was scheduled and the drift would
# otherwise go unreported. See drift_advisory for why this is a
# message rather than an exit.
def _reload_and_drift_items(reload_action: str, recommended: list[str]) -> None:
    if reload_action:
        recommended.append(reload_action)
    if not (_common.test_cases_only() and _DIST_UPDATE_REPO):
        _drift = setup_reload_report.drift_advisory()
        if _drift:
            recommended.append(_drift)
