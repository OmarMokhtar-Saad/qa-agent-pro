"""One tool call: telemetry, log line and audit around the handler coroutine."""

from __future__ import annotations

import asyncio

from handlers import _lane
from handlers.mcp_srv_drift import _inflight_exit
from handlers.mcp_srv_progress import keepalive
from handlers.mcp_srv_reply_log import log_reply
from handlers.mcp_srv_toolcall import _capture_error, _close_tool_call, _open_tool_call
from tools import dispatch_guard, tool_briefs

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
# The reply TEXT is logged separately, bounded and scrubbed, by
# `mcp_srv_reply_log.log_reply` (G11); the handler's own inputs stay out.
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
        return await _kept(name, ctx, coro)
    except asyncio.CancelledError:
        ok = False
        error_type = "cancelled"
        raise
    except Exception as exc:
        ok = False
        error_type = type(exc).__name__
        _capture_error(exc, name)
        raise
    finally:
        await _close_then_release(
            name, start, (ok, error_type, status_token, dispatch_token)
        )


# A cancelled call is NOT an Exception, so `_tracked` used to log it "ok"; it
# now logs "error cancelled" (fix/1520). Under anyio's level-triggered cancel
# every later await raises again, so an unshielded close died at its audit
# await and the dispatch/in-flight slots never came back. The close runs as
# its own task; the slots are released in `_tracked`'s own task (this
# coroutine is awaited there, in the same context) because the dispatch token
# is a ContextVar token and resets only in the context that made it, which a
# shielded task (a context copy) is not.
async def _close_then_release(name, start, run) -> None:
    """Close the call in a task that outlives a cancel, then give the slots back."""
    ok, error_type, status_token, dispatch_token = run
    try:
        await asyncio.shield(
            _close_tool_call(name, start, (ok, error_type, status_token))
        )
    finally:
        dispatch_guard.exit_dispatched(dispatch_token)
        _inflight_exit()


async def _tracked_noted(name, ctx, coro):
    """``_tracked`` plus the tool's own note (``tools/tool_briefs.TOOL_NOTES``)
    appended to a text reply. ``_tracked`` itself stays a pure pass-through."""
    return tool_briefs.append_tool_note(name, await _tracked(name, ctx, coro))


async def _tracked_mobile(name, ctx, coro):
    """``_tracked`` for a mobile tool: a stop reply ends with the host's way on
    (``tools/mobile/host_way_on``, fix/1490)."""
    return _lane.host_way_on.with_way_on(name, await _tracked(name, ctx, coro))


async def _tracked_mobile_noted(name, ctx, coro):
    """``_tracked_noted`` plus the host's way on after a stop reply."""
    return _lane.host_way_on.with_way_on(name, await _tracked_noted(name, ctx, coro))


async def _kept(name, ctx, coro):
    """Await *coro* with the call's ONE keepalive running beside it (fix/1580 C01)."""
    async with keepalive(name, ctx):
        return log_reply(name, await coro)
