"""The Feature Analysis SUBMIT half: close the task and render the report."""

from __future__ import annotations

from agents.feature_analysis import finalize_feature_report
from handlers import _common
from handlers._boundary import handler_guard
from handlers.feature_analysis_gate import _fa_disabled_reply
from handlers.feature_analysis_report import _fa_report_reply
from handlers.feature_analysis_resubmit import _fa_resubmit_reply
from handlers.feature_analysis_types import _FaClosed
from tools import host_llm as _host_llm
from tools.error_boundary import text_reply


def _fa_close_error(error: str) -> str:
    return (
        f"⚠️ {error}. Start again with `qa_feature_analysis` — "
        "a task id is one-shot and expires with the prep TTL."
    )


async def handle_submit_feature_analysis(
    task_id: str, report_json: str, *, progress: _common.ProgressCb = None
) -> str:
    """SUBMIT half of the chat-only Feature Analysis report. Never raises.

    The host wrote the report; this server coerces the UNTRUSTED payload into a
    ``FeatureAnalysisReport`` (unknown keys dropped, no fabrication), renders it
    and records the audit row -- the same side effects the server always owned.
    """
    # Defence in depth -- see handle_feature_analysis (2026-08-03).
    reply = _fa_disabled_reply()
    if reply:
        return reply
    task_id = (task_id or "").strip()
    if not task_id:
        return "⚠️ Pass the `task_id` from `qa_feature_analysis`."
    if not (report_json or "").strip():
        return "⚠️ Send the Feature Analysis JSON you wrote as `report_json`."
    return await _submit_report(task_id, report_json, progress)


@handler_guard(
    "qa_submit_feature_analysis",
    "handle_submit_feature_analysis failed",
    text_reply("⚠️ Feature Analysis submission failed: "),
)
async def _submit_report(
    task_id: str, report_json: str, progress: _common.ProgressCb
) -> str:

    await _common.emit(progress, "📋 Checking the Feature Analysis report…")
    closed = await _host_llm.close_task(
        task_id, report_json, expect_kind="feature_analysis"
    )
    if closed.get("error"):
        return _fa_close_error(closed["error"])
    content = closed.get("content") or {}
    meta = content.get("meta") or {}
    report, usable = finalize_feature_report(content.get("payload"))
    resubmit = await _fa_resubmit_reply(task_id, meta, usable)
    if resubmit is not None:
        return resubmit
    return await _fa_report_reply(_FaClosed(task_id, meta, report, usable))
