"""The Feature Analysis submit reply: audit, header and the rendered report."""

from __future__ import annotations

from agents.feature_analysis import render_report_markdown
from handlers import _common
from handlers.feature_analysis_file import _write_feature_analysis_file
from handlers.feature_analysis_types import _FaClosed


def _fa_report_header(mode: str, screens_n: int, usable: bool) -> str:
    header = "## Feature Analysis\n\n"
    if mode and mode != "jira":
        header += f"_Mode: {mode} · screens captured: {screens_n}_\n\n"
    if not usable:
        header += (
            "> ⚠️ The submitted JSON carried no usable report, so every "
            "section below is empty. Call `qa_feature_analysis` again to "
            "retry.\n\n"
        )
    return header


async def _fa_audit_report(closed: _FaClosed) -> None:
    meta = closed.meta
    await _common.audit(
        "mcp_feature_analysis",
        entity_id=closed.task_id,
        detail={
            "mode": str(meta.get("mode") or ""),
            "source": str(meta.get("source") or ""),
            "screens": int(meta.get("screens") or 0),
            "round": int(meta.get("round") or 1),
            "usable": closed.usable,
        },
    )


# Audit the submission and render the report, with a note on the saved file.
async def _fa_report_reply(closed: _FaClosed) -> str:
    meta = closed.meta
    await _fa_audit_report(closed)
    mode = str(meta.get("mode") or "")
    header = _fa_report_header(mode, int(meta.get("screens") or 0), closed.usable)
    markdown = header + render_report_markdown(closed.report)
    file_note = await _write_feature_analysis_file(closed.task_id, markdown)
    return header + file_note + render_report_markdown(closed.report, compact=True)
