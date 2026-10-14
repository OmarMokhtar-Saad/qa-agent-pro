"""Per-tool trace id and the extra-properties bag (never raise)."""

from __future__ import annotations

import logging
import uuid

from config.settings_guard import never_raise
from tools.telem_host import host

logger = logging.getLogger("qa_agents.telemetry")


def start_tool_trace(tool: str) -> str:
    """Begin a per-tool trace: set a fresh ``$ai_trace_id`` and an empty extra-
    properties bag so LLM generations during this call link to it and handlers
    can attach content-free properties. Returns the trace id. Never raises."""
    trace_id = uuid.uuid4().hex
    # never-raise contract
    with never_raise(logger, "telemetry: start_tool_trace failed", exc_info=True):
        host()._ai_trace_id_var.set(trace_id)
        host()._tool_props_var.set({})
    return trace_id


def current_trace_id() -> str:
    """The in-flight tool's ``$ai_trace_id`` ('' outside a tool call). Never raises."""
    # never-raise contract
    with never_raise():
        return host()._ai_trace_id_var.get()
    return ""


def add_tool_properties(**props) -> None:
    """Attach content-free properties (e.g. case_count / format / source) to the
    in-flight ``tool_called`` event. No-op outside a tool trace. Never raises."""
    # never-raise contract
    with never_raise(logger, "telemetry: add_tool_properties failed", exc_info=True):
        bag = host()._tool_props_var.get()
        if bag is None:
            return
        for key, value in props.items():
            if value is not None:
                bag[key] = value


def pop_tool_properties() -> dict:
    """Return and clear the in-flight tool's extra-properties bag. Never raises."""
    # never-raise contract
    with never_raise():
        bag = host()._tool_props_var.get()
        host()._tool_props_var.set(None)
        return dict(bag) if bag else {}
    return {}
