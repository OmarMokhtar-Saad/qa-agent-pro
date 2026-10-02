"""Log the tool calls FastMCP rejects before ``mcp_server._tracked`` ever runs.

A call whose arguments fail validation never reaches ``_tracked`` (validation
happens inside the framework, before the tool body), so the server log said
nothing while the tester's client saw an error. This middleware sits in front
of validation and writes ONE WARNING line per rejection.

WHAT IS LOGGED: the tool name (sanitised), the exception class, and the
pydantic error TYPES (``int_parsing``, ``missing``, ``unexpected_keyword_argument``).
NEVER a value: types only, no ``input``, no ``ctx``, no ``loc`` (a location is a
caller-chosen key name), and never ``exc_info`` -- a traceback prints the
ValidationError, and its text carries the rejected input.

Version note: written against fastmcp 2.14.7, where the ValidationError
propagates out of ``call_next``. The cause chain is walked so a framework that
wraps it in a ToolError still logs. ``build_middleware`` returns None when the
Middleware base is not importable, and the caller also guards on
``hasattr(mcp, "add_middleware")``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("qa_agents.mcp")


def _validation_error(exc: BaseException | None):
    """The pydantic ValidationError in ``exc``'s cause chain, or None."""
    try:
        from pydantic import ValidationError
    except Exception:
        return None
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
    try:
        rows = found.errors(
            include_input=False, include_context=False, include_url=False
        )
        return sorted({str(row.get("type") or "unknown") for row in rows})
    except Exception:
        return ["unreadable"]


def _safe_name(name: object) -> str:
    cleaned = "".join(ch for ch in str(name or "") if ch.isalnum() or ch in "_-.")
    return cleaned or "unknown"


def log_rejection(tool: object, exc: BaseException) -> bool:
    """Write the rejection line; False (and silence) when ``exc`` is not one."""
    kinds = error_types(exc)
    if not kinds:
        return False
    logger.warning(
        "tool %s: arguments rejected before the handler ran (%s): %s",
        _safe_name(tool),
        type(exc).__name__,
        ",".join(kinds),
    )
    return True


def build_middleware():
    """A FastMCP middleware that logs rejections, or None if unsupported."""
    try:
        from fastmcp.server.middleware import Middleware
    except Exception:
        return None

    class _RejectionLog(Middleware):
        async def on_call_tool(self, context, call_next):
            try:
                return await call_next(context)
            except Exception as exc:
                try:
                    name = getattr(getattr(context, "message", None), "name", "")
                    log_rejection(name, exc)
                except Exception:
                    # A constant message and NO exc_info: the active exception
                    # chain carries the rejected input.
                    logger.debug("rejection log failed")
                raise

    return _RejectionLog()
