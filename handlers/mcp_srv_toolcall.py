"""Per-call bookkeeping for ``_tracked``: open, settle, log, audit, telemetry."""

from __future__ import annotations

import time
from typing import NamedTuple

from config.settings_guard import never_raise
from handlers import mcp_srv_state
from handlers.mcp_srv_client import _note_client
from handlers.mcp_srv_drift import _inflight_enter
from handlers.mcp_srv_state import logger
from tools import audit_log, audit_port, dispatch_guard, telemetry, tool_status
from tools.telemetry import ErrorReport, ToolCall


class _ToolOutcome(NamedTuple):
    """One settled tool call: what the log, audit and telemetry lines report."""

    name: str
    ok: bool
    error_type: str | None
    degraded: bool
    duration_ms: int


def _device_ref(device_id: str = "", serial: str = "") -> str:
    """ONE device reference from the two names a caller may use.

    ``device_id`` is the canonical parameter on every device-taking tool and
    ``serial`` is accepted as an alias for the same value (a model that read
    one tool's schema guessed the other's name). Both given and different is a
    contradiction the server will not resolve by guessing which phone to drive,
    so it raises; FastMCP returns the message to the caller as the tool error.
    """
    first = str(device_id or "").strip()
    second = str(serial or "").strip()
    if first and second and first != second:
        raise ValueError(
            "device_id and serial name different devices (device_id=%r, "
            "serial=%r). Pass ONE of them: serial is an alias for device_id."
            % (first, second)
        )
    return first or second


def _settle_outcome(ok, error_type, status_token):
    """Fold a device timeout into the outcome: (ok, error_type, degraded).

    A handler that RETURNED after a device timeout is not `ok`: the Air run
    logged "tool qa_mobile_test: ok in 47589 ms" over a screencap that had
    timed out after 30 s (fix round 3, item 3). A raised exception keeps its
    own error type."""

    timed_out = tool_status.finish(status_token)
    degraded = ok and bool(timed_out)
    if degraded:
        ok = False
        error_type = ("timed_out:" + ",".join(sorted(set(timed_out))))[:120]
    return ok, error_type, degraded


def _log_tool_line(o: _ToolOutcome) -> None:
    """The one INFO line per call that names the tool."""
    logger.info(
        "tool %s: %s in %d ms",
        o.name,
        "ok"
        if o.ok
        else (
            o.error_type if o.degraded else "error %s" % (o.error_type or "Exception")
        ),
        o.duration_ms,
    )


def _audit_detail(o: _ToolOutcome) -> dict:
    status = "timed_out" if o.degraded else ("ok" if o.ok else "error")
    return {
        "tool": o.name,
        "duration_ms": o.duration_ms,
        "ok": o.ok,
        "error_type": o.error_type,
        "status": status,
    }


async def _audit_tool_call(o: _ToolOutcome) -> None:
    """Best-effort ``tool_called`` audit event; never raises."""
    with never_raise(logger, "audit_log record_event failed", exc_info=True):
        await audit_port.emit(
            audit_log.AuditEvent(
                event_type="tool_called",
                actor=mcp_srv_state.client_info().get("name", "") or None,
                detail=_audit_detail(o),
            )
        )


def _emit_tool_telemetry(o: _ToolOutcome) -> None:
    telemetry.tool_called(
        ToolCall(
            o.name,
            duration_ms=o.duration_ms,
            ok=o.ok,
            error_type=o.error_type,
            client_name=mcp_srv_state.client_info().get("name", ""),
            client_version=mcp_srv_state.client_info().get("version", ""),
            extra=telemetry.pop_tool_properties(),
        )
    )


def _capture_error(exc, name) -> None:
    telemetry.capture_error_dist(ErrorReport(exc, tool=name, origin="mcp_tool"))


def _open_tool_call(name, ctx) -> tuple:
    """Everything `_tracked` does before the handler runs: (start, status
    token, dispatch token).

    The client identity is read HERE so every tool (qa_host_check included)
    logs and tags its real editor; it used to be read only from
    `_make_progress`, which tools that report no progress never reached. Never
    raises (see `_note_client`). The status token is a per-call sink for facts
    a returned handler must not report as `ok` -- an adb screencap or dump that
    timed out (fix round 3, item 3)."""

    _note_client(ctx)
    start = time.monotonic()
    status_token = tool_status.begin()
    telemetry.start_tool_trace(name)
    _inflight_enter()
    return start, status_token, dispatch_guard.enter_dispatched()


async def _close_tool_call(name, start, run) -> None:
    """Everything `_tracked` does after the handler: settle, log, audit, emit.

    The dispatch and in-flight slots are released by `_tracked` itself, LAST
    and in its own task: releasing them earlier would let a drift restart fire
    while telemetry and FastMCP's result serialization still had work to do,
    and os._exit does not flush buffered stdout.

    Runs shielded (fix/1520), i.e. in a COPY of the caller's context, so
    `tool_status.finish` cannot reset the marks token there (it swallows the
    ValueError) and the caller's context keeps its marks list until the next
    `begin()` replaces it. Harmless, and known."""
    ok, error_type, status_token = run
    duration_ms = int((time.monotonic() - start) * 1000)
    ok, error_type, degraded = _settle_outcome(ok, error_type, status_token)
    o = _ToolOutcome(name, ok, error_type, degraded, duration_ms)
    _log_tool_line(o)
    await _audit_tool_call(o)
    _emit_tool_telemetry(o)
