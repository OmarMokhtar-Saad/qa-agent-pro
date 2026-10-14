"""Once-per-process degradation WARNING of tools.host_llm (split out of host_llm.py)."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from tools import host_llm_disclosure as dis

logger = logging.getLogger("tools.host_llm")

WARNED = False


def warn_once_if_degraded() -> str:
    """Log the degradation WARNING once per process. Returns what it logged.

    Called from the MCP server's startup so an operator who flips the kill switch
    before the ledger is migrated sees it immediately, in the log, rather than
    discovering it when the ambiguity gate stops flagging. Fires on the EXPLICIT
    ``degraded`` boolean from ``disclosure_state`` -- not on the note's leading
    warning sign -- so it also covers the allowed-but-unmigrated bypass state and
    cannot be silenced by a future wording change. Never raises.
    """
    global WARNED
    # defensive
    with never_raise():
        note, degraded = dis.disclosure_state()
        if WARNED or not degraded or not note:
            return ""
        WARNED = True
        logger.warning("%s", note)
        return note
    return ""
