"""The Feature Analysis prepare step: open the task and render the host reply."""

from __future__ import annotations

from agents.feature_analysis import prepare_feature_analysis
from agents.feature_analysis_request import FeatureAnalysisRequest
from handlers import _common, explore_bug
from handlers.feature_analysis_header import _fa_reply_header
from handlers.feature_analysis_types import (
    FeatureAnalysisCall,
    FeatureAnalysisReply,
    _FaCapture,
    _FaTicket,
)
from tools import telemetry


def _fa_source(ticket: _FaTicket, capture: _FaCapture) -> str:
    return (
        "jira" if ticket.used_url else "mobile" if capture.screens_captured else "text"
    )


def _fa_prepare_request(
    call: FeatureAnalysisCall, ticket: _FaTicket, capture: _FaCapture
) -> FeatureAnalysisRequest:
    return FeatureAnalysisRequest(
        call.feature_or_url or "Feature captured from mobile device screens.",
        ticket.text,
        "",
        mode=call.mode,
        screens=capture.screens_captured,
        source=_fa_source(ticket, capture),
    )


def _fa_prepare_detail(mode: str, ticket: _FaTicket, screens: int) -> dict:
    return {
        "mode": mode,
        "source": "url" if ticket.used_url else "text",
        "screens": screens,
    }


async def _fa_audit_prepare(task_id: str, detail: dict) -> None:
    """Audit the opened Feature Analysis task."""
    await _common.audit(
        "mcp_feature_analysis_prepare", entity_id=task_id, detail=detail
    )


# F16 (2026-08-30): `shape_host_task`'s response_schema is opt-IN; without
# it the host guesses field names and a wrong guess fails INVISIBLY
# (finalize_feature_report drops unknown keys by design).
def _fa_opened_reply(
    mode: str, capture: _FaCapture, opened_content: dict
) -> FeatureAnalysisReply:
    """Render the opened task as the host reply, attaching the screens."""
    task_id = str(opened_content.get("task_id") or "")
    envelope = opened_content.get("envelope") or {}
    return FeatureAnalysisReply(
        _fa_reply_header(mode, capture)
        + explore_bug.shape_host_task(
            explore_bug.HostTaskSpec(
                "Feature Analysis — your turn",
                task_id,
                "qa_submit_feature_analysis",
                "field `report_json`",
                response_schema=envelope.get("response_schema"),
            ),
            envelope,
        ),
        list(capture.forwarded),
    )


async def _fa_prepare_reply(
    call: FeatureAnalysisCall, ticket: _FaTicket, capture: _FaCapture
) -> str | FeatureAnalysisReply:
    """Open the analysis task, audit it and render the reply for the host."""
    await _common.emit(
        call.progress, "📋 Preparing the Feature Analysis task for your chat model…"
    )
    opened = await prepare_feature_analysis(_fa_prepare_request(call, ticket, capture))
    if opened.get("error"):
        return f"⚠️ Feature Analysis preparation failed: {opened['error']}"
    content = opened.get("content") or {}
    telemetry.add_tool_properties(source=_fa_source(ticket, capture))
    detail = _fa_prepare_detail(call.mode, ticket, capture.screens_captured)
    await _fa_audit_prepare(str(content.get("task_id") or ""), detail)
    return _fa_opened_reply(call.mode, capture, content)
