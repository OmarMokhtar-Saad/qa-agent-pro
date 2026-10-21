"""Log the tool calls FastMCP rejects before ``mcp_server._tracked`` ever runs.

A call whose arguments fail validation never reaches ``_tracked`` (validation
happens inside the framework, before the tool body), so the server log said
nothing while the tester's client saw an error. This middleware sits in front
of validation and writes ONE WARNING line per rejection.

WHAT IS LOGGED: the tool name (sanitised), the exception class, and the
pydantic error TYPES (``int_parsing``, ``missing``, ``unexpected_keyword_argument``).
plus the offending argument NAMES (first ``loc`` element, sanitised and capped:
a name is caller-chosen text, so it is cut, never trusted).
NEVER a value: no ``input``, no ``ctx``, and never ``exc_info`` -- a traceback prints the
ValidationError, and its text carries the rejected input.

Version note: written against fastmcp 2.14.7, where the ValidationError
propagates out of ``call_next``. The cause chain is walked so a framework that
wraps it in a ToolError still logs. ``build_middleware`` returns None when the
Middleware base is not importable, and the caller also guards on
``hasattr(mcp, "add_middleware")``.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from config.settings_guard import never_raise
from tools import mcp_arg_tolerance
from tools import mcp_rejection_hint as rejection_hint

try:
    from fastmcp.server.middleware import Middleware as _Middleware

    Middleware: Any = _Middleware
except ImportError as exc:  # no fastmcp, or one without middleware
    if (exc.name or "").split(".")[0] != "fastmcp":
        raise
    Middleware = None

try:
    from fastmcp.exceptions import ToolError as _ToolError

    ToolError: Any = _ToolError
except ImportError:  # no fastmcp
    ToolError = None

logger = logging.getLogger("qa_agents.mcp")
MAX_NAMES = 8
MAX_NAME_CHARS = 40


def _validation_error(exc: BaseException | None):
    """The pydantic ValidationError in ``exc``'s cause chain, or None."""
    seen: set[int] = set()
    current = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, ValidationError):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def error_types(exc: BaseException | None) -> list[str]:
    """Sorted, de-duplicated pydantic error types; [] when this is no rejection."""
    found = _validation_error(exc)
    if found is None:
        return []
    with never_raise():
        rows = found.errors(
            include_input=False, include_context=False, include_url=False
        )
        return sorted({str(row.get("type") or "unknown") for row in rows})
    return ["unreadable"]


def _safe_name(name: object) -> str:
    cleaned = "".join(ch for ch in str(name or "") if ch.isalnum() or ch in "_-.")
    return cleaned or "unknown"


def error_args(exc: BaseException | None) -> list[str]:
    """Sorted argument names the rejection points at (names only, never values)."""
    found = _validation_error(exc)
    if found is None:
        return []
    with never_raise():
        rows = found.errors(
            include_input=False, include_context=False, include_url=False
        )
        locs = [r.get("loc") or () for r in rows]
        names = {str(loc[0]) for loc in locs if loc and isinstance(loc[0], str)}
        return sorted(_safe_name(n)[:MAX_NAME_CHARS] for n in names)[:MAX_NAMES]
    return []


def log_rejection(tool: object, exc: BaseException) -> bool:
    """Write the rejection line; False (and silence) when ``exc`` is not one."""
    kinds = error_types(exc)
    if not kinds:
        return False
    logger.warning(
        "tool %s: arguments rejected before the handler ran (%s): %s [args: %s]",
        _safe_name(tool),
        type(exc).__name__,
        ",".join(kinds),
        ",".join(error_args(exc)) or "-",
    )
    return True


def _note_rejection(context: object, exc: BaseException) -> None:
    """Log the rejection of the tool call in ``context``; never raises."""
    # A constant message and NO exc_info: the active exception
    # chain carries the rejected input.
    with never_raise(logger, "rejection log failed"):
        name = getattr(getattr(context, "message", None), "name", "")
        log_rejection(name, exc)


async def _fixable(context: object, exc: BaseException) -> BaseException | None:
    """A ToolError the model can act on for a rejection, else None."""
    found = _validation_error(exc)
    if found is None or ToolError is None:
        return None
    with never_raise():
        name = str(getattr(getattr(context, "message", None), "name", "") or "")
        tool = None
        fm = getattr(getattr(context, "fastmcp_context", None), "fastmcp", None)
        if fm is not None:
            tool = await fm.get_tool(name)
        return ToolError(rejection_hint.fix_message(_safe_name(name), found, tool))
    return None


def build_middleware():
    """A FastMCP middleware that logs rejections, or None if unsupported."""
    if Middleware is None:
        return None

    class _RejectionLog(Middleware):
        async def on_call_tool(self, context, call_next):
            dropped = await mcp_arg_tolerance.tolerate(context)
            with never_raise() as g:
                result = await call_next(context)
                if dropped:
                    result = mcp_arg_tolerance.add_note(result, dropped)
                return result
            assert g.exc is not None
            _note_rejection(context, g.exc)
            raise await _fixable(context, g.exc) or g.exc

    return _RejectionLog()
