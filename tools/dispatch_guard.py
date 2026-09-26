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

v1.97.0 cursor-hardening (item 3d): ``_DISPATCHED`` now holds a per-process
``secrets.token_bytes(32)`` nonce (``_NONCE``) instead of the literal
``True``, so the NAIVE one-liner bypass -- a bare ``_DISPATCHED.set(True)``,
copy-pasted without reading this file's own value -- no longer satisfies
``require_dispatched``. This closes exactly that one gap and no more: a
side-channel process that spawns ITS OWN real ``mcp_server._tracked()``
dispatch (item 6's audited attack) still dispatches through the genuine path
and is unaffected by this change -- catching THAT is item 6's detection
work, not this guard's.
"""

from __future__ import annotations

import contextvars
import secrets

_NONCE: bytes = secrets.token_bytes(32)

_DISPATCHED: "contextvars.ContextVar[object]" = contextvars.ContextVar(
    "qa_agents_dispatched", default=None
)


class DispatchGuardRefusal(RuntimeError):
    """Raised when a state-mutating handler is called without going through
    ``mcp_server._tracked()`` first."""


def enter_dispatched() -> contextvars.Token:
    """Called by ``mcp_server._tracked()`` right after ``_inflight_enter()``,
    before awaiting the tool's coroutine. Returns the reset token."""
    return _DISPATCHED.set(_NONCE)


def exit_dispatched(token: contextvars.Token) -> None:
    """Called by ``mcp_server._tracked()`` in its ``finally``, right before
    ``_inflight_exit()`` -- a paired lifetime with ``enter_dispatched``."""
    _DISPATCHED.reset(token)


def require_dispatched(tool_name: str) -> None:
    """First statement in each of the four state-mutating handlers. Raises
    ``DispatchGuardRefusal`` naming ``tool_name`` unless the calling frame is
    inside a real ``mcp_server._tracked()`` dispatch.

    Compares BY IDENTITY (``is _NONCE``), not truthiness: a bare
    ``_DISPATCHED.set(True)`` -- the naive one-liner bypass this item exists
    to close -- is truthy but is not ``_NONCE`` and so still raises.
    """
    if _DISPATCHED.get() is not _NONCE:
        raise DispatchGuardRefusal(
            f"{tool_name} was called without going through the MCP dispatch "
            "path (mcp_server._tracked). Call the qa_* tool through your MCP "
            "client instead of importing tools.mcp_handlers directly."
        )
