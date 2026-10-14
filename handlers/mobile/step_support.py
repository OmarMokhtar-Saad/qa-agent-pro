"""Small helpers of the mobile step handlers: heartbeat, learning, failure text."""

from __future__ import annotations

import asyncio
import logging

from config.settings_guard import never_raise
from handlers import _common
from handlers.mobile.step_state import _MOBILE_LEARN_TASKS, _MOBILE_TOOL_FAILURES
from tools.mobile import app_tools, knowledge_runner, session_runs
from tools.mobile import heartbeat as mobile_heartbeat
from tools.untrusted import wrap_untrusted

logger = logging.getLogger("tools.mcp_handlers")


def _mobile_start_heartbeat(run_id: str, token: str) -> None:
    """Keep the run's lease fresh while the tester thinks. Never raises.

    Best-effort by design: the heartbeat is a courtesy to the OTHER chat, and a
    run must not fail because a background writer could not start.
    """
    with never_raise(logger, "mobile heartbeat could not start", exc_info=True):
        mobile_heartbeat.start(run_id, token)


def _mobile_learn_after(run_id: str) -> None:
    """Schedule per-app learning for a finished run. Never raises, never waits."""
    try:
        if not run_id or not _common.mobile_lane_enabled():
            return

        body = (session_runs.resolve(run_id) or {}).get("content") or {}
        package = str(body.get("package") or "")
        if not package:
            return
        task = asyncio.get_running_loop().create_task(
            knowledge_runner.after_result(package, run_id)
        )
        _MOBILE_LEARN_TASKS.add(task)
        task.add_done_callback(_MOBILE_LEARN_TASKS.discard)
    except _MOBILE_TOOL_FAILURES:
        logger.exception("mcp mobile: could not schedule learning for %s", run_id)


def _mobile_tool_failed(exc: BaseException, tool: str) -> str:
    """The text a mobile tool returns when it fails; the cause is logged."""
    logger.exception("%s failed", tool)
    _common.capture_error(exc, tool)
    return (
        "⚠️ `"
        + tool
        + "` failed ("
        + type(exc).__name__
        + "). Check `qa_mobile_status` before retrying."
    )


def _mobile_unresolved(noun: str, res) -> str:
    """The question for a device or app pick that did not settle on one value."""

    options = ", ".join(str(getattr(o, "key", o)) for o in tuple(res.options or ()))
    question = str(res.question or "") or ("Ask the user which " + noun + " to use.")
    body = question + ("\nOptions: " + options if options else "")
    return (
        "⚠️ The "
        + noun
        + " is not settled. Do not guess; ask the user. Nothing was touched.\n"
        + wrap_untrusted("mobile_pick", body, app_tools.MAX_TOOL_TEXT_CHARS)
    )
