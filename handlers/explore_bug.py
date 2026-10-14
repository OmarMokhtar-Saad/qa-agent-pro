"""Chat-only bug report and exploratory coaching handlers (facade over explore_bug_*)."""

from __future__ import annotations

import logging

from handlers import _common, export
from handlers import explore_bug_coach as _coach
from handlers import explore_bug_prepare as _prepare
from handlers import explore_bug_submit as _submit
from handlers.explore_bug_session import ExploreStepOpts
from handlers.explore_bug_task import HostTaskSpec, shape_host_task

__all__ = [
    "ExploreStepOpts",
    "HostTaskSpec",
    "handle_bug_report",
    "handle_explore_step",
    "handle_submit_bug_report",
    "handle_submit_explore_step",
    "shape_host_task",
]

logger = logging.getLogger("tools.mcp_handlers")


async def handle_bug_report(
    description: str, *, progress: _common.ProgressCb = None
) -> str:
    """PREPARE half of the chat-only bug report; see explore_bug_prepare."""
    if _common.test_cases_only():
        return export._TEST_CASES_ONLY_NOTICE
    description = (description or "").strip()
    if not description:
        return "⚠️ Describe the bug in plain language."
    try:
        return await _prepare.open_bug_report(description, progress)
    except Exception as exc:
        logger.exception("handle_bug_report failed")
        return f"⚠️ Bug-report generation failed: {exc}"


async def handle_explore_step(
    feature: str, session_id: str, opts: ExploreStepOpts | None = None
) -> str:
    if _common.test_cases_only():
        return export._TEST_CASES_ONLY_NOTICE
    session_id = (session_id or "").strip()
    if not session_id:
        return "⚠️ Provide a stable session_id so the coach can track this session."
    try:
        return await _prepare.open_explore_step(feature, session_id, opts)
    except Exception as exc:
        logger.exception("handle_explore_step failed")
        return f"⚠️ Exploratory coaching failed: {exc}"


async def handle_submit_bug_report(
    task_id: str, report: str, *, progress: _common.ProgressCb = None
) -> str:
    """SUBMIT half of the chat-only bug report. Never raises."""
    if _common.test_cases_only():
        return export._TEST_CASES_ONLY_NOTICE
    task_id = (task_id or "").strip()
    if early := _submit.input_error(task_id, report):
        return early
    try:
        return await _submit.submit_bug_report(task_id, report, progress)
    except Exception as exc:
        logger.exception("handle_submit_bug_report failed")
        return f"⚠️ Bug-report submission failed: {exc}"


async def handle_submit_explore_step(
    task_id: str, step: str, *, progress: _common.ProgressCb = None
) -> str:
    """SUBMIT half of the chat-only coaching turn. Never raises."""
    if _common.test_cases_only():
        return export._TEST_CASES_ONLY_NOTICE
    task_id = (task_id or "").strip()
    if early := _coach.input_error(task_id, step):
        return early
    try:
        return await _coach.submit_coach_step(task_id, step)
    except Exception as exc:
        logger.exception("handle_submit_explore_step failed")
        return f"⚠️ Exploratory coaching failed: {exc}"
