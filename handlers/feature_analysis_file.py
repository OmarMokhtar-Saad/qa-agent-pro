"""Write the FULL Feature Analysis report beside the other exports."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from config.settings_guard import never_raise
from handlers import export

logger = logging.getLogger("tools.mcp_handlers")


def _fa_report_path(export_dir: str, task_id: str) -> Path:
    """The new report file path inside the export dir (created if missing)."""
    dest = Path(export_dir).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    safe_id = (
        "".join(ch for ch in str(task_id or "") if ch.isalnum() or ch in "-_")[:32]
        or "report"
    )
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return dest / f"qa_feature_analysis_{safe_id}_{stamp}.md"


def _fa_file_note(path: Path) -> str:
    return (
        f"**Full report (all sections):** `{path}`\n\n"
        "The summary below is the short view; the file has the requirement "
        "analysis, assumptions, UI analysis, user flow and conflicts.\n\n"
    )


# Write the FULL Feature Analysis report beside the other exports.
#
# F1 (2026-08-30). The submit reply renders the report COMPACT -- four of its
# sixteen sections -- and ``render_report_markdown`` justifies that mode on
# the grounds that "the full analysis is delivered as a downloadable file".
# No caller ever wrote one. Twelve sections the rubric spends seven numbered
# steps asking the host for -- every functional requirement, every acceptance
# criterion, the preconditions, roles, dependencies, validation rules, error
# handling, edge cases, the assumptions, the UI analysis, the user flow, and
# the conflicts the rubric says to "never silently drop" -- were submitted,
# validated and thrown away, with nothing in the reply to say so.
#
# Returns a markdown line naming the path, or "" when there is no configured
# export dir or the write fails: a missing file must never cost the tester the
# report body. Never raises.
async def _write_feature_analysis_file(task_id: str, markdown: str) -> str:
    with never_raise(
        logger,
        "could not write the Feature Analysis file",
        level=logging.WARNING,
        exc_info=True,
    ):
        export_dir = export.resolved_export_dir()
        if not export_dir:
            return ""
        path = _fa_report_path(export_dir, task_id)
        await asyncio.to_thread(path.write_text, markdown, encoding="utf-8")
        # LOGGED, not audited: the mcp_feature_analysis row is this submission's
        # audit event and tests pin it as the last one, so a second row here
        # would make the artifact write look like the submission.
        logger.info("feature analysis report written to %s", path)
        return _fa_file_note(path)
    return ""
