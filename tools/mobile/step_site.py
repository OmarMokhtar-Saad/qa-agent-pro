"""Name the code site that caused a dump, for the per-step ledger.

The ledger records milliseconds and a reason; the next live audit also needs to
know WHICH wait asked. A phase or mark is recorded inside the awaiting caller's
frames, so the first frame outside the dump plumbing is the site.
"""

from __future__ import annotations

import sys

#: Module-name prefixes (last dotted part) that are dump plumbing, never the site.
#: ``mcp_`` frames are the MCP middleware wrapped around EVERY tool call (it
#: awaits the whole step, replay time included), so it names no phase.
_PLUMBING = ("step_", "dump", "screen_anchor", "screen_probe", "mcp_")
_NAME_CHARS = 40


def _is_site(module: str) -> bool:
    tail = module.rsplit(".", 1)[-1]
    return module.startswith("tools.") and not tail.startswith(_PLUMBING)


def caller() -> str:
    """``module.function`` of the nearest non-plumbing frame, or ``""``."""
    frame = sys._getframe(1)
    while frame is not None:
        module = str(frame.f_globals.get("__name__", ""))
        if _is_site(module):
            return f"{module.rsplit('.', 1)[-1]}.{frame.f_code.co_name}"[:_NAME_CHARS]
        frame = frame.f_back  # type: ignore[assignment]
    return ""
