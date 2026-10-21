"""Report reply of the mobile step handlers: relay block, summary and report link."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from tools.mobile import (
    act_scrub,
    finish_evidence,
    finish_secret_scan,
    knowledge_close,
    media,
    session_finish,
    session_records,
    session_runs,
    step_timing,
)
from tools.mobile import open_report as mobile_open
from tools.mobile import render as mobile_render
from tools.mobile import report as mobile_report
from tools.mobile.replay import stop_shot

logger = logging.getLogger("tools.mcp_handlers")


def _mobile_render_report_file(run_id: str) -> tuple[str, bool, str]:
    """Render and open the report: ``(path, opened, failure_text)``. Never raises."""
    try:
        produced = mobile_report.render(run_id)
        if produced.get("error"):
            return "", False, str(produced["error"])
        path = str((produced.get("content") or {}).get("path") or "")
        opened = (mobile_open.open_report(path) or {}).get("content")
        return path, bool((opened or {}).get("opened")), ""
    except Exception:  # never-raise: a report is not a verdict
        logger.exception("mcp mobile report render failed")
        return (
            "",
            False,
            "the report renderer failed; the verdicts below are unaffected",
        )


#: Background report renders still running (held so they are not collected).
_RENDERS: set = set()


#: The last line of a finished run's reply: stops the chat's status/stop/status.
RUN_OVER_NOTE = (
    "\n\nRun finished; device released; no qa_mobile_status/qa_mobile_stop needed."
)


# The run is over: give the emulator back. THE HOLDER releasing its
# own lock -- there is no reaper in this lane, and nothing here could
# take a lock from another process even if it wanted to. Under the
# lease this call is driving with, so a chat that has been displaced
# since cannot free the new holder's device on its way out.
#
# The SECOND consumer of the verdict-coverage value, and the one a
# tester reads to answer "did this run pass": ``summary_block``'s tally
# would otherwise let a STATUS stand in for a VERDICT. ``resolved`` is
# carried by BOTH STATE_REPORT paths (``session.next_packet``).
async def _finish_run(run_id: str, session_token: str, body: dict) -> tuple:
    """``(resolved, rows, coverage)`` once the device is given back."""

    await stop_shot.drain_all()  # B11: the report reads these PNGs
    # deferred clips need the device: store them first
    await step_timing.timed("media_flush", media.flush(run_id))
    finishing = session_finish.finish_device(run_id, lease=str(session_token or ""))
    await step_timing.timed("finish_device", finishing)
    listed = step_timing.timed_call("run_summary", session_runs.summary, run_id)
    resolved = body.get("resolved")
    rows_for_relay = listed.get("content") or []
    coverage = session_runs.coverage_line(
        resolved if isinstance(resolved, dict) else {}, rows_for_relay
    )
    return resolved, rows_for_relay, coverage


def _relay_kwargs(resolved: Any, rows_for_relay: list) -> dict[str, Any]:
    """The verdict and reset arguments of the relay block."""

    return {
        "verdict": session_records.run_verdict(resolved, rows_for_relay),
        "reset": session_records.reset_step_status(resolved, rows_for_relay),
    }


# ``partial`` stays a hardcoded LIFECYCLE fact: this is reached only
# when ``next_packet`` reports STATE_REPORT. It decides ONE word of the
# heading and claims nothing about verdicts (the coverage line does).
def _render_later(run_id: str) -> str:
    """Render and open the report AFTER the reply (a ~3 s webp derivation the
    tester does not wait for); the path is known up front. Never raises."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return _mobile_render_report_file(run_id)[0]
    task = loop.run_in_executor(None, _mobile_render_report_file, run_id)
    _RENDERS.add(task)
    task.add_done_callback(_RENDERS.discard)
    return str(mobile_report.report_path(run_id))


def _summary_kwargs(run_id: str, coverage: str) -> dict[str, Any]:
    """The summary block arguments; the report file is rendered lazily."""
    report_path = step_timing.timed_call("report_schedule", _render_later, run_id)
    report_opened, report_failed = False, ""
    return {
        "coverage_line": coverage,
        "run_id": run_id,
        "partial": False,
        "report_path": report_path,
        "report_opened": report_opened,
        "report_error": report_failed,
    }


def _finish_extras_sync(run_id: str, rows: list) -> str:
    know = knowledge_close.learn_line(run_id)
    know = "\n\nKnowledge: " + know if know else ""
    return (
        finish_secret_scan.scan_line(run_id)
        + finish_evidence.evidence_table(run_id, rows)
        + know
    )


async def _finish_extras(run_id: str, rows: list) -> str:
    """The secret-scan line and the claim-vs-evidence table (off the event loop)."""
    return await asyncio.to_thread(_finish_extras_sync, run_id, rows)


async def _mobile_report_text(run_id: str, session_token: str, body: dict) -> str:
    """The reply for a run that is over: relay block, summary and report link."""

    resolved, rows, coverage = await _finish_run(run_id, session_token, body)
    summary = _summary_kwargs(run_id, coverage)
    relay = mobile_render.relay_block(
        **_relay_kwargs(resolved, rows),
        typed_field_line=mobile_render.typed_field_tally(rows),
    )
    text = (
        relay
        + mobile_render.summary_block(rows, mobile_render.SummaryOpts(**summary))
        + await _finish_extras(run_id, rows)
        + RUN_OVER_NOTE
    )
    act_scrub.drop(run_id)  # the scan above is remembered; a re-render reuses it
    return text
