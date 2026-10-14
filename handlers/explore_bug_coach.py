"""SUBMIT half of the chat-only coaching step (explore_bug leaf)."""

from __future__ import annotations

from handlers import _agents, _common, _reply, export
from handlers.explore_bug_session import _record_covered_area
from tools import host_llm as _host_llm


def input_error(task_id: str, step: str) -> str | None:
    """The early-return text for a missing task_id or step, else None."""
    if not task_id:
        return "⚠️ Pass the `task_id` from `qa_explore_step`."
    if not (step or "").strip():
        return "⚠️ Send the coaching step you wrote as `step`."
    return None


def _session_lost_reply(clean: str) -> str:
    """The step text plus the note that its session memory is gone."""
    return (
        clean + "\n\n_This coaching session's memory is no longer available (the "
        "server restarted, or the session was never started). Call "
        "`qa_explore_step` with a session_id to begin a tracked session._"
    )


async def _apply_step(
    session_id: str, sess: dict, step_result: tuple[str, dict | None]
) -> str:
    """Record the host's step in the session, audit it, and shape the reply."""
    clean, coach_meta = step_result
    sess["history"].append({"role": "assistant", "content": clean})
    _record_covered_area(sess, coach_meta)
    await _common.audit(
        "mcp_explore_step",
        entity_id=session_id,
        detail={"turns": (sess.get("memory") or {}).get("turn_count", 0)},
    )
    return _reply.shape_explore_step(session_id, sess, clean)


async def _close_step(task_id: str, step: str) -> dict:
    """Close the host task for a coaching step (raises like host_llm does)."""

    return await _host_llm.close_task(task_id, step, expect_kind="explore_step")


async def submit_coach_step(task_id: str, step: str) -> str:
    """Close the coaching task and fold the host's step into its session."""
    closed = await _close_step(task_id, step)
    if closed.get("error"):
        return (
            f"⚠️ {closed['error']}. Start again with `qa_explore_step` "
            "— a task id is one-shot and expires with the prep TTL."
        )
    content = closed.get("content") or {}
    meta = content.get("meta") or {}
    # The session id comes from the RECORD, never from a tool parameter, so a
    # host cannot write findings into another tester's session.
    session_id = str(meta.get("session_id") or "")
    step_result = _agents.finalize_coach_step(str(content.get("raw") or ""))
    sess = export._SESSIONS.get(session_id)
    if sess is None:
        return _session_lost_reply(step_result[0])
    return await _apply_step(session_id, sess, step_result)
