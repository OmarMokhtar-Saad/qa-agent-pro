"""Startup steps of ``mcp_server.main``, in the order the facade calls them."""

from __future__ import annotations

import threading

from config.settings_guard import never_raise
from handlers import mcp_srv_settings
from handlers.mcp_srv_state import logger
from tools import telemetry
from tools.host_llm import warn_once_if_degraded
from tools.log_setup_env import INSTALL_ROOT
from tools.wire_tap import install_stdin_tap


def gate_open() -> bool:
    """True when ``QA_MCP_ENABLED`` lets the server start; else warn and say no."""
    if not mcp_srv_settings.mcp_enabled():
        logger.warning(
            "QA_MCP_ENABLED is off — the MCP server will not start. "
            "Set QA_MCP_ENABLED=true in .env to enable it."
        )
        return False
    return True


# Host-boomerang migration: if an operator flipped QA_SERVER_LLM_ENABLED off
# while ledger rows are still unmigrated, those features are OFF rather than
# boomeranged. Say so once, at startup, instead of letting it surface as the
# ambiguity gate quietly stopping. No-op (and silent) with the flag ON.
def disclose_degraded_llm() -> None:
    """Warn once at startup when the server-LLM flag is off with unmigrated rows."""
    with never_raise(logger, "server-LLM disclosure failed", exc_info=True):
        warn_once_if_degraded()


def announce_startup() -> None:
    """Emit the startup telemetry notice."""
    telemetry.startup_notice()


def announce_server_start() -> None:
    """Emit the server-start telemetry event."""
    telemetry.server_start()


# _prewarm_backend stood here until 2026-08-16 (dead-code deletion P2-G2b).
# It was a daemon thread calling llm._cursor_usable() to warm the
# cursor-agent auth probe -- up to 20s -- off the serving path. P2-G2c
# deletes all three backends, so there is no probe to warm; the bare
# `except` around it is exactly why this had to be deleted deliberately
# rather than left to fail silently.
def start_drift_thread(target) -> None:
    """Start the drift watcher (the facade's ``_drift_watch``) as a daemon."""
    threading.Thread(target=target, daemon=True).start()


def install_wire_tap() -> None:
    """Log one line per request read from stdin (method, id, size; never params)."""
    with never_raise(logger, "wire tap not installed"):
        install_stdin_tap(INSTALL_ROOT)
