"""Wall-clock stamps for one MCP call: request received, reply sent.

B12 measures the host-side gap (tool time minus the server's own line). The
host reports its own start and end; these stamps give the server's two edges
so the gap splits into "before the server saw it", "inside the server" and
"after the server replied". Report only; nothing here changes a reply.
"""

from __future__ import annotations

import time
from contextvars import ContextVar

_STAMPS: ContextVar = ContextVar("_CALL_STAMPS", default=None)


def received() -> None:
    """Stamp the request edge for the current task (first stamp wins)."""
    if _STAMPS.get() is None:
        _STAMPS.set({"wall": time.time(), "mono": time.monotonic()})


def reply_sent() -> dict:
    """``{"received_at", "replied_at", "server_ms"}`` or ``{}`` if unstamped."""
    state = _STAMPS.get()
    if state is None:
        return {}
    ms = int((time.monotonic() - state["mono"]) * 1000)
    return {
        "received_at": round(state["wall"], 3),
        "replied_at": round(state["wall"] + ms / 1000, 3),
        "server_ms": ms,
    }


def outside_ms(call_ms: int, counted_ms: int) -> int:
    """Stamped call time (request to reply) the step timer did not count (never negative)."""
    return max(0, int(call_ms) - int(counted_ms))


def clear() -> None:
    """Forget the stamps: the next call stamps afresh."""
    _STAMPS.set(None)
