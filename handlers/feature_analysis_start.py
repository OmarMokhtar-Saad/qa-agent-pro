"""The Feature Analysis PREPARE half: gate, mode, ticket, capture, prepare."""

from __future__ import annotations

import dataclasses
import logging

from handlers import _common
from handlers.feature_analysis_capture import _fa_capture_stage
from handlers.feature_analysis_gate import _fa_disabled_reply
from handlers.feature_analysis_modes import (
    _fa_input_gate_reply,
    _fa_normalize_mode,
    _fa_resolve_mode,
)
from handlers.feature_analysis_prepare import _fa_prepare_reply
from handlers.feature_analysis_ticket import _fa_fetch_ticket
from handlers.feature_analysis_types import FeatureAnalysisCall

logger = logging.getLogger("tools.mcp_handlers")


async def _fa_run(call: FeatureAnalysisCall) -> str:
    """Ticket, capture and prepare for a gated call; a failure becomes a reply."""
    try:
        ticket, reply = await _fa_fetch_ticket(call)
        if reply:
            return reply
        capture, reply = await _fa_capture_stage(call)
        if reply:
            return reply
        return await _fa_prepare_reply(call, ticket, capture)
    except Exception as exc:
        logger.exception("handle_feature_analysis failed")
        _common.capture_error(exc, "qa_feature_analysis")
        return f"⚠️ Feature analysis failed: {exc}"


# The server makes NO model call for the ANALYSIS. It still runs the mode menu
# (jira / mobile / jira_mobile), the device pick and capture-another screenshot
# loop on the mobile modes, and then ATTACHES the captured screens to the
# reply as MCP image content -- since 2026-08-15 there is no server-side
# vision call on this path at all; the tester's own multimodal model reads
# the screens -- then hands that model a task envelope. The written report comes
# back through `qa_submit_feature_analysis`, which coerces the untrusted
# payload, renders it and records the delivered-artifact audit row. A plain
# call with only feature_or_url (no mode, no elicitation) still covers the
# original single-shot text/Jira analysis, now in two steps. Never raises.
async def handle_feature_analysis(call: FeatureAnalysisCall) -> str:
    """PREPARE half of the chat-only standalone Feature Analysis (Phase 4)."""
    reply = _fa_disabled_reply()
    if reply:
        return reply
    text = (call.feature_or_url or "").strip()
    mode, reply = await _fa_resolve_mode(
        _fa_normalize_mode(call.mode), text, call.choose
    )
    reply = reply or _fa_input_gate_reply(mode, text)
    if reply:
        return reply
    return await _fa_run(dataclasses.replace(call, feature_or_url=text, mode=mode))
