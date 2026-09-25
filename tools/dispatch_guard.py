"""Marks the frame that is currently awaiting a qa_* tool's coroutine inside
``mcp_server._tracked()``. The four state-mutating handlers named in the
Air onboarding FIX-BRIEF (``handle_generate_test_cases``,
``handle_prepare_test_cases``, ``handle_submit_suite``,
``handle_submit_category``) call ``require_dispatched`` as their first
statement and refuse to run at all unless this ContextVar is set.

THIS IS DETERRENCE, NOT A SECURITY BOUNDARY. A local agent with filesystem
access can read this file and either patch it out or call
``_DISPATCHED.set(True)`` itself before importing a handler -- nothing
server-side can tell that apart from a real MCP call once the attacker
controls the same interpreter. It raises the cost of an in-process bypass
from "one import" to "read and patch a file", and only works paired with the
FIX-BRIEF's server-instructions rule (P0-1) telling the agent the qa_* tools
are the only supported path -- the pairing is the point, not either half
alone.
"""

from __future__ import annotations

import contextvars

_DISPATCHED: "contextvars.ContextVar[bool]" = contextvars.ContextVar(
    "qa_agents_dispatched", default=False
)


class DispatchGuardRefusal(RuntimeError):
    """Raised when a state-mutating handler is called without going through
    ``mcp_server._tracked()`` first."""


def enter_dispatched() -> contextvars.Token:
    """Called by ``mcp_server._tracked()`` right after ``_inflight_enter()``,
    before awaiting the tool's coroutine. Returns the reset token."""
    return _DISPATCHED.set(True)


def exit_dispatched(token: contextvars.Token) -> None:
    """Called by ``mcp_server._tracked()`` in its ``finally``, right before
    ``_inflight_exit()`` -- a paired lifetime with ``enter_dispatched``."""
    _DISPATCHED.reset(token)


def require_dispatched(tool_name: str) -> None:
    """First statement in each of the four state-mutating handlers. Raises
    ``DispatchGuardRefusal`` naming ``tool_name`` unless the calling frame is
    inside a real ``mcp_server._tracked()`` dispatch."""
    if not _DISPATCHED.get():
        raise DispatchGuardRefusal(
            f"{tool_name} was called without going through the MCP dispatch "
            "path (mcp_server._tracked). Call the qa_* tool through your MCP "
            "client instead of importing tools.mcp_handlers directly."
        )
