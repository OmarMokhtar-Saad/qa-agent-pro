"""Request parameter object of tools.host_llm (split out of host_llm.py)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HostTaskRequest:
    """What a caller asks of ``open_task`` / ``build_envelope``.

    ``build_envelope`` ignores ``meta`` and ``created_by``: they are server-side
    record state that never reaches the envelope.
    """

    kind: str
    system: str
    user: str
    return_field: str = ""
    response_schema: dict | None = None
    meta: dict | None = None
    created_by: str | None = None
    submit_tool: str = ""
