"""One tool call: telemetry, log line and audit around the handler coroutine."""

from __future__ import annotations

from handlers.mcp_srv_toolcall import _capture_error, _close_tool_call, _open_tool_call
from tools import tool_briefs

# Await a tool handler while emitting a best-effort telemetry
# ``tool_called`` event (name, duration, ok/error_type, host client) and, on
# the dist path, a scrubbed ``capture_error_dist`` on failure. A per-tool
# ``$ai_trace_id`` is set so LLM ``$ai_generation`` events link to this call.
# Telemetry NEVER changes behaviour: the result or exception propagates
# unchanged and any metric failure is swallowed in the telemetry layer.
#
# It also writes the ONE log line that names the tool (2026-08-19, F07).
# Before it, the only per-call trace in ``data/logs/qa-agents-<pid>.log`` was
# the MCP library's nameless ``Processing request of type CallToolRequest``:
# a 12-call live run left 11 calls unattributable, and a forensics script that
# looked for tool names in the log found none and picked no log at all. The
# line belongs HERE, not in each handler, because this is the one seam every
# tool already passes through and the name is a literal at each call site.
#
# WHY IT CANNOT LEAK A PAYLOAD: this function is handed a NAME and an already
# built coroutine. Ticket text, generated cases and test data are bound inside
# that coroutine and are not values this frame can see, so the containment is
# structural rather than a rule about what to format. Keep it that way -- do
# not add an ``args`` parameter to satisfy a future 'just log the prep_id'.
#
# One line, INFO, per call: the file already reaches ~220 KB on a busy install,
# and ``mcp.server.lowlevel.server`` is silenced in ``_configure_logging`` so
# the per-call volume is unchanged rather than doubled.


async def _tracked(name, ctx, coro):
    """Await a tool handler, reporting its outcome (see the comment above)."""
    start, status_token, dispatch_token = _open_tool_call(name, ctx)
    ok = True
    error_type = None
    try:
        return await coro
    except Exception as exc:
        ok = False
        error_type = type(exc).__name__
        _capture_error(exc, name)
        raise
    finally:
        await _close_tool_call(
            name, start, (ok, error_type, status_token, dispatch_token)
        )


async def _tracked_noted(name, ctx, coro):
    """``_tracked`` plus the tool's own note (``tools/tool_briefs.TOOL_NOTES``)
    appended to a text reply. ``_tracked`` itself stays a pure pass-through."""
    return tool_briefs.append_tool_note(name, await _tracked(name, ctx, coro))
