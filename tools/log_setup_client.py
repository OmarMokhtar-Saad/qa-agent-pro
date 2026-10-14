"""The MCP client tag carried on every per-process log line.

Part of the ``tools.log_setup`` facade; see its docstring for the design.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from tools.log_setup_base import _MAX_CLIENT_CHARS, UNKNOWN_CLIENT, process_log_path

logger = logging.getLogger("qa_agents.log_setup")

_client = {"name": UNKNOWN_CLIENT}


def _clean_client_name(name: object) -> str:
    """The host-supplied client name, sanitised and capped (``unknown`` if empty)."""
    raw = str(name or "").strip().lower()
    cleaned = "".join(ch for ch in raw if ch.isalnum() or ch in "-_.")
    return cleaned[:_MAX_CLIENT_CHARS] or UNKNOWN_CLIENT


# Record the MCP client that owns this process, for the per-line tag.
#
# Called from ``mcp_server._note_client`` with the handshake's
# ``clientInfo.name``. Sanitised hard: the value is host-supplied and ends up in
# every log line, where a newline would forge a log entry. Never raises.
def set_client(name: object, log_dir: str | Path | None = None) -> str:
    """Record the MCP client that owns this process, for the per-line tag."""
    try:
        resolved = _clean_client_name(name)
        changed = resolved != _client.get("name")
        _client["name"] = resolved
        if changed and resolved != UNKNOWN_CLIENT:
            # One greppable client -> file mapping. Without it a diagnoser has to
            # read INTO each file to find out which editor owns which pid.
            logger.info(
                "client=%s owns %s",
                resolved,
                process_log_path(log_dir) if log_dir else f"pid {os.getpid()}",
            )
    except (OSError, TypeError, ValueError):
        logger.debug("could not record the MCP client name", exc_info=True)
        _client["name"] = UNKNOWN_CLIENT
    return _client["name"]


def current_client() -> str:
    """The client tag for this process (``unknown`` before the handshake)."""
    return _client.get("name") or UNKNOWN_CLIENT


class ProcessContextFilter(logging.Filter):
    """Attach ``qa_client`` to every record.

    The pid needs no filter: ``process`` is a standard LogRecord attribute."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.qa_client = current_client()
        return True
