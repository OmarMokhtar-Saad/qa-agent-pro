"""Public telemetry emitters: tool_called, capture_error_dist, capture_ai_generation."""

from __future__ import annotations

import logging

from config.settings_guard import never_raise
from tools.telem_args import AiGeneration, ErrorReport, ToolCall
from tools.telem_host import host

logger = logging.getLogger("qa_agents.telemetry")


def _merge(props: dict, extra: dict | None) -> None:
    """Copy the non-None entries of *extra* into *props*."""
    for key, value in (extra or {}).items():
        if value is not None:
            props[key] = value


def tool_called(call: ToolCall) -> None:
    """Emit a ``tool_called`` event. Never raises; carries only the exception
    CLASS name on failure (``error_type``), never message content. ``extra``
    holds content-free, handler-supplied properties (case_count / export format
    / source type) collected via the per-tool props bag."""
    h = host()
    props = h._base_properties(call.client_name, call.client_version)
    props["tool"] = call.tool
    props["duration_ms"] = int(call.duration_ms)
    props["ok"] = bool(call.ok)
    if call.error_type:
        props["error_type"] = str(call.error_type)
    _merge(props, call.extra)
    h.capture("tool_called", props)


def _error_props(report: ErrorReport) -> dict:
    props = host()._base_properties()
    props["error_type"] = type(report.exc).__name__
    if report.tool:
        props["tool"] = str(report.tool)
    if report.origin:
        props["origin"] = str(report.origin)
    tid = host().current_trace_id()
    if tid:
        props["$ai_trace_id"] = tid
    _merge(props, report.properties)
    return props


def _send_error(h, report: ErrorReport, props: dict) -> None:
    client = h.dist_sdk_client()
    if client is not None:
        client.capture_exception(
            h._scrubbed_exception(report.exc),
            distinct_id=h._distinct_id(),
            properties=props,
        )
    else:
        h.dispatch(h.build_payload("tool_error", h._distinct_id(), props))


# Report a dist-path exception to PostHog error tracking. Gated on the DIST
# key (``enabled``). Privacy: only the exception
# CLASS name and stack FRAMES are sent -- the message and absolute frame
# paths are scrubbed (see ``_scrub_event_paths``). Never raises.
# With the SDK present it groups via ``capture_exception``; without it a
# best-effort bare-HTTP ``tool_error`` event (class name only) is sent.
def capture_error_dist(report: ErrorReport) -> None:
    """Report a dist-path exception to PostHog error tracking (never raises)."""
    h = host()
    if not h.enabled():
        return
    # never-raise contract
    with never_raise(logger, "telemetry: capture_error_dist failed", exc_info=True):
        _send_error(h, report, _error_props(report))


def _token_props(props: dict, g: AiGeneration) -> None:
    if g.input_tokens is not None:
        props["$ai_input_tokens"] = int(g.input_tokens)
    if g.output_tokens is not None:
        props["$ai_output_tokens"] = int(g.output_tokens)
    if g.latency_s is not None:
        props["$ai_latency"] = round(float(g.latency_s), 3)


def _gen_props(g: AiGeneration) -> dict:
    props = host()._base_properties()
    props["$ai_model"] = str(g.model or "")
    props["$ai_provider"] = str(g.provider or "")
    _token_props(props, g)
    props["estimated"] = bool(g.estimated)
    if g.method:
        props["method"] = str(g.method)
    props["ok"] = bool(g.ok)
    if g.error_type:
        props["error_type"] = str(g.error_type)
    tid = g.trace_id or host().current_trace_id()
    if tid:
        props["$ai_trace_id"] = tid
    return props


def capture_ai_generation(gen: AiGeneration) -> None:
    """Emit a PostHog LLM-analytics ``$ai_generation`` event for one LLM call.

    Content-free by contract: ``$ai_input`` / ``$ai_output_choices`` (prompt and
    completion text) are NEVER included -- only metadata (model, provider, token
    counts, latency, trace id). Gated on the DIST key (``enabled``). Never raises.
    """
    h = host()
    if not h.enabled():
        return
    # never-raise contract
    with never_raise(logger, "telemetry: capture_ai_generation failed", exc_info=True):
        h.capture("$ai_generation", _gen_props(gen))
