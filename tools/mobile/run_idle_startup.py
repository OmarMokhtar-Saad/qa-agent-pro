"""Close runs a previous session left unfinished, once, as the server starts.

Without this the sweep only ran when the next run was created, so a stale run
sat "open" until then. This runs once on the server's own event loop (no timer,
no polling) and never raises: a failed sweep is logged and the server serves on.
"""

from __future__ import annotations

import logging

from tools.mobile import run_idle_close, session_finish

logger = logging.getLogger("tools.mobile.session")


async def close_orphans() -> list[str]:
    """Sweep stale-lease runs at start-up; the closed run ids."""
    try:
        ports = session_finish.timeout_ports()
        return await run_idle_close.close_dead_runs("", ports, only_dead=True)
    except (OSError, ValueError, TypeError, KeyError):
        logger.warning("run_idle_startup: start-up sweep failed", exc_info=True)
        return []
