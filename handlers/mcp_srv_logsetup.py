"""Process logging setup: INFO to a per-process file, WARNING+ to stderr."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

from config.settings_guard import never_raise
from handlers.mcp_srv_state import logger
from tools.process_env import setdefault_env

# Optional on purpose: this is the first statement of main(), so a whitelist
# regression in the dist build (the module absent) must not kill the server
# before a single line could say why. Any OTHER import failure still raises.
try:
    import tools.log_setup as log_setup
except ImportError as exc:
    if not (exc.name or "").startswith("tools.log_setup"):
        raise
    log_setup = cast("Any", None)


def _add_stderr_handler(root: logging.Logger) -> logging.Handler:
    root.setLevel(logging.INFO)
    stderr_handler = logging.StreamHandler()
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    root.addHandler(stderr_handler)
    return stderr_handler


def _open_file_log(log_dir: Path):
    """The file handler and ``process_log_path`` (or ``(None, None)``)."""
    with never_raise():
        # ``log_setup`` is None when the module is absent: the attribute error
        # lands in the guard and reads as "no file log" (reported by the caller).
        return log_setup.configure_file_logging(log_dir), log_setup.process_log_path
    return None, None


def _report_file_log(handler, path_for, log_dir: Path) -> None:
    if handler is None:
        logger.warning(
            "Could not open a log file under data/logs -- keeping INFO on stderr."
        )
    else:
        logger.info("logging to %s", path_for(log_dir))


# Third-party request logging is diagnostic noise at INFO (one line per
# telemetry POST); real problems still surface at WARNING+. FastMCP is in
# the list because it attaches its OWN rich handler (bypassing the root
# config above), so its INFO transport banner still reached stderr on the
# v1.38.0 validation run.
#
# ``mcp.server.lowlevel.server`` joined the list on 2026-08-19 (F07): its
# INFO line is "Processing request of type CallToolRequest" and names
# nothing, so it cost one line per call and told a diagnoser nothing.
# ``_tracked`` now logs a NAMED line for every call, which is what that line
# was standing in for -- dropping it keeps per-call volume flat.
#
# The setLevel is NOT enough for FastMCP: it (re)configures its own
# handler and level during server.run(), which overrode this on the
# v1.39.0 validation (the INFO transport banner still hit stderr at
# 09:09:36). Its level is read from the environment, so pin it there --
# setdefault keeps an operator's explicit choice.
def _quiet_noisy_loggers() -> None:
    """Silence third-party INFO noise (httpx, httpcore, FastMCP, mcp lowlevel)."""
    for noisy in ("httpx", "httpcore", "FastMCP", "mcp.server.lowlevel.server"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    setdefault_env("FASTMCP_LOG_LEVEL", "WARNING")


# INFO+ to THIS PROCESS's own file under data/logs/; WARNING+ to stderr.
#
# Over stdio, MCP clients render EVERY stderr line as an error (Cursor logs
# "[error] INFO ..." for each httpx/telemetry line), which buries real
# failures in noise. Errors stay on stderr; the full INFO trail moves to a
# file an operator can tail. Never raises -- if the file handler cannot be
# created, stderr keeps INFO so nothing is lost.
#
# 2026-08-09: the file is PER-PROCESS (``qa-agents-<pid>.log``). One install
# is shared by up to three MCP clients, and a shared RotatingFileHandler had
# three processes rotating one name -- each rollover stranded the other two on
# a rotated-away inode, so a 15:08 finalize wrote its audit rows and its xlsx
# while the log's entries stopped at 15:04. Every line now carries the pid and
# (once the initialize handshake has happened) the client name, so a diagnoser
# can attribute it. See tools/log_setup.py for the full mechanism.
def _configure_logging() -> None:
    """INFO+ to this process's own file under data/logs/; WARNING+ to stderr."""
    stderr_handler = _add_stderr_handler(logging.getLogger())
    log_dir = Path(__file__).resolve().parents[1] / "data" / "logs"
    handler, path_for = _open_file_log(log_dir)
    if handler is None:
        stderr_handler.setLevel(logging.INFO)
    _report_file_log(handler, path_for, log_dir)
    _quiet_noisy_loggers()
