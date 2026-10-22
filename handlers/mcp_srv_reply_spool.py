"""The full text of a long tool reply, kept in its own bounded, private log file (L-5/O-6).

The server log keeps a short excerpt of every reply; a reply longer than that
excerpt is also written, whole, to ``replies-<pid>.log`` next to it so an audit
can read what the host was told. Guarantees: the text passes the same scrubber
as the excerpt (scrub parity, over the WHOLE text in one pass, then capped), the
file is mode 0600, and it is bounded (1 MB x 2 per process, swept like the other
logs). When the file cannot be opened, or there is no scrubber, nothing is
written and no pointer is returned.
"""

from __future__ import annotations

import logging
from pathlib import Path

from config.settings_guard import never_raise
from handlers import _lane
from tools import log_setup

SPOOL_CAP = 64_000
SPOOL_PREFIX = "replies"
LOG_DIR = Path(__file__).resolve().parents[1] / "data" / "logs"

spool_logger = logging.getLogger("qa_agents.reply_spool")
spool_logger.propagate = False
spool_logger.setLevel(logging.INFO)  # written whatever the root level is
_state: dict[str, Path | None] = {}


def reset() -> None:
    """Forget the open file (tests, and a changed ``LOG_DIR``)."""
    for handler in list(spool_logger.handlers):
        spool_logger.removeHandler(handler)
        handler.close()
    _state.clear()


def _open() -> Path | None:
    """Create the 0600 file and attach its rotating handler; None if it cannot open."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = log_setup.process_log_path(LOG_DIR, SPOOL_PREFIX)
    handler = log_setup.configure_file_logging(
        LOG_DIR,
        prefix=SPOOL_PREFIX,
        target=spool_logger,
        max_bytes=1_000_000,
        backup_count=1,
        private=True,
    )
    return path if handler is not None else None


def _path() -> Path | None:
    """The spool file path, opened on first use (a failed open is remembered)."""
    if "path" not in _state:
        _state["path"] = None
        _state["path"] = _open()
    return _state["path"]


def spool(name: str, text: str) -> str:
    """Write the scrubbed *text* of reply *name*; its `` full=<file>`` pointer, or ``""``."""
    if _lane.scrub_render is None:  # a dist without the mobile lane has no scrubber
        return ""
    with never_raise(logging.getLogger(__name__), "reply spool failed"):
        path = _path()
        if path is not None:
            clean = _lane.scrub_render.scrub_text(_lane.act_scrub.scrub(text))
            spool_logger.info("reply %s:\n%s", name, clean[:SPOOL_CAP])
            return f" full={path.name}"
    return ""
