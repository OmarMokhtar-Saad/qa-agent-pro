"""Parameter objects for the public telemetry emitters (build with keywords)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolCall:
    """One ``tool_called`` event."""

    tool: str
    duration_ms: int = 0
    ok: bool = True
    error_type: object = None
    client_name: str = ""
    client_version: str = ""
    extra: dict | None = None


@dataclass(frozen=True)
class AiGeneration:
    """One ``$ai_generation`` event (metadata only, never prompt text)."""

    model: str = ""
    provider: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_s: float | None = None
    estimated: bool = False
    trace_id: str | None = None
    method: str = ""
    ok: bool = True
    error_type: str | None = None


@dataclass(frozen=True)
class ErrorReport:
    """One dist-path exception report."""

    exc: BaseException
    tool: str | None = None
    origin: str | None = None
    properties: dict | None = None
