"""PREPARE halves of the chat-only bug report and coaching step (explore_bug leaf)."""

from __future__ import annotations

from handlers import _agents, _common
from handlers.explore_bug_session import (
    ExploreStepOpts,
    _explore_session_update,
)
from handlers.explore_bug_task import HostTaskSpec, shape_host_task


async def _bug_task_reply(content: dict, chars: int) -> str:
    """Audit the opened bug-report task and shape it for the host."""
    task_id = str(content.get("task_id") or "")
    await _common.audit(
        "mcp_bug_report_prepare", entity_id=task_id, detail={"chars": chars}
    )
    spec = HostTaskSpec(
        "Bug report — your turn", task_id, "qa_submit_bug_report", "field `report`"
    )
    return shape_host_task(spec, content.get("envelope") or {})


# PREPARE half of the chat-only bug report (host-boomerang Phase 2).
#
# The server makes NO model call. It does the RAG enrichment and the untrusted
# wrapping, then hands the tester's own chat model a task envelope; the written
# report comes back through `qa_submit_bug_report`, which validates the section
# markers and seeds the corpus. Never raises.
async def open_bug_report(description: str, progress: _common.ProgressCb) -> str:
    """PREPARE half of the chat-only bug report: open the host task."""
    await _common.emit(
        progress, "🐛 Preparing the bug-report task for your chat model…"
    )
    opened = await _agents.prepare_bug_report(description)
    if opened.get("error"):
        return f"⚠️ Bug-report preparation failed: {opened['error']}"
    return await _bug_task_reply(opened.get("content") or {}, len(description))


async def _coach_task_reply(opened: dict, session_id: str, sess: dict) -> str:
    """Audit the opened coaching task and shape it for the host."""
    content = opened.get("content") or {}
    task_id = str(content.get("task_id") or "")
    await _common.audit(
        "mcp_explore_step_prepare",
        entity_id=session_id,
        detail={"turns": sess["memory"].get("turn_count", 0)},
    )
    spec = HostTaskSpec(
        "Exploratory coaching — your turn",
        task_id,
        "qa_submit_explore_step",
        "field `step`",
    )
    return shape_host_task(spec, content.get("envelope") or {})


async def open_explore_step(
    feature: str, session_id: str, opts: ExploreStepOpts | None
) -> str:
    """PREPARE half of one coaching step; the facade owns the never-raises guard."""
    opts = opts or ExploreStepOpts()
    sess = _explore_session_update(session_id, feature, opts.tester_response)
    await _common.emit(
        opts.progress, "🧭 Preparing the coaching step for your chat model…"
    )
    opened = await _agents.prepare_coach_step(
        sess["feature"],
        sess["history"],
        _agents.CoachStepOptions(session_memory=sess["memory"], session_id=session_id),
    )
    if opened.get("error"):
        return f"⚠️ Exploratory coaching failed: {opened['error']}"
    return await _coach_task_reply(opened, session_id, sess)
