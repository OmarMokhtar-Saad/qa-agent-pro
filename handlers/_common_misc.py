"""Moved from handlers (refactor P2): _common."""

from __future__ import annotations

import logging
from pathlib import Path

from config.settings_guard import never_raise
from handlers._common_choice import ProgressCb
from tools import telemetry
from tools.telemetry import ErrorReport

# Pinned to the pre-split name: log filters, caplog and handlers key on it.
logger = logging.getLogger("tools.mcp_handlers")


# Actor recorded on every MCP audit event. The MCP transport carries no
# logged-in tester identity, so all its events share this actor.
_ACTOR = "mcp"

# Install root (…/qa-agents), used to anchor a RELATIVE qa_export_dir so the
# exported deliverable lands in the same folder whichever MCP client launched
# the server. See resolved_export_dir.
INSTALL_ROOT = Path(__file__).resolve().parent.parent


async def emit(progress: ProgressCb, message: str) -> None:
    """Best-effort progress ping — a failing/absent callback never breaks a tool."""
    if progress is None:
        return
    with never_raise(
        logger, "mcp progress callback failed for %r", message, exc_info=True
    ):
        await progress(message)


def _is_url(text: str) -> bool:
    return text.lower().startswith(("http://", "https://"))


def capture_error(exc: BaseException, tool: str) -> None:
    """Best-effort dist error capture for a handler failure. Never raises; inert
    in the private checkout / with telemetry off (no dist PostHog key)."""
    with never_raise(logger, "mcp telemetry capture_error failed", exc_info=True):
        telemetry.capture_error_dist(ErrorReport(exc, tool=tool, origin="mcp_handler"))
