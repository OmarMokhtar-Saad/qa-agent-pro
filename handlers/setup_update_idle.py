"""Defer a qa-doctor update while a host session is live (G07)."""

from __future__ import annotations

from handlers import mcp_srv_drift
from handlers.mcp_srv_state import _INFLIGHT, _INFLIGHT_LOCK


def session_busy() -> bool:
    """True while another tool call or a live mobile run depends on this process.

    qa-doctor counts itself as one in-flight call, so more than one means a
    different call is running."""
    with _INFLIGHT_LOCK:
        others = int(_INFLIGHT["n"]) > 1
    return others or mcp_srv_drift._probed_runs() > 0


def deferred_update_advisory(status: str) -> str:
    """The qa-doctor line for an update that was found but not installed."""
    if status != "available":
        return ""
    return (
        "A newer release is available. It was NOT installed now because a "
        "session is active -- an update would restart the server under it. "
        "It installs when the server is idle (run `qa-doctor` again then) "
        "or on the next start."
    )
