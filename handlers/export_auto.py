"""Best-effort Excel auto-export (moved out of handlers.export; MCP path only)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Callable

from agents.host_submit_export import AutoExportRequest
from config.settings_guard import never_raise
from handlers import _common
from handlers.export_auto_dir import _auto_export_ask_dir, _auto_export_target
from handlers.export_hooks import export

logger = logging.getLogger("tools.mcp_handlers")


# Hands the caller the path the tester actually got,
# including an elicited custom folder. Added so the
# Zephyr pair could land beside the workbook; that
# exporter was DELETED on 2026-08-15 (batch D4) and
# handle_submit_suite still reports the path.
def _hand_on_path(on_path: Callable[[str], None], path: str) -> None:
    with never_raise(logger, "auto-export on_path callback failed", exc_info=True):
        on_path(path)


async def _auto_export_write(
    suite, output_path: str | None, on_path: Callable[[str], None] | None
) -> str:
    """Write the workbook (explicit path or secure temp) and hand on the path."""
    if output_path is not None:
        path = await asyncio.to_thread(
            export.generate_test_case_xlsx, suite, output_path
        )
    else:
        path = await asyncio.to_thread(export._EXPORTERS["xlsx"], suite)
    if on_path is not None:
        _hand_on_path(on_path, path)
    return path


# This path IS the deliverable, and the only channel an MCP tool result
# has for it is TEXT -- so it has to survive a chat model that
# paraphrases the reply instead of quoting it. Hence the explicit
# show-this-verbatim instruction, and hence the callers placing this
# block FIRST in their response rather than after the suite body: on
# 2026-08-03 a real run generated 98 cases and exported cleanly, and the
# tester never saw a path.
def _ready_text(path: str, link: str) -> str:
    return (
        "### \U0001f4c4 Your Excel file is ready\n\n"
        "**\U0001f4c2 SHOW THIS PATH TO THE TESTER, VERBATIM — it is "
        "the deliverable they asked for:**\n\n"
        f"`{path}`\n\n"
        f"{link}"
        "Open it (macOS):\n\n"
        f'```bash\nopen "{path}"\n```\n\n'
        "_That spreadsheet is the finished deliverable — nothing else to "
        "run. Need CSV, Gherkin, Playwright or TestRail instead? Just say "
        "so._"
    )


async def _auto_export_reply(path: str, reject_note: str) -> str:
    """The reply block for a written export: the path, a link and how to open it."""
    try:
        uri = Path(path).as_uri()
    except (ValueError, OSError):
        uri = ""
    await _common.audit("mcp_auto_export_xlsx", detail={"path": path})
    link = f"[Open the file]({uri})\n\n" if uri else ""
    return _ready_text(path, link) + reject_note


async def _export_destination(request: AutoExportRequest) -> tuple[str, str]:
    """The save folder (empty = secure temp) and the note to append, if any."""
    export_dir = export.resolved_export_dir()
    if request.ask_text is not None and not export_dir:
        # F1a (2026-08-10): ASK ONLY WHEN THERE IS NO ANSWER ALREADY. With
        # QA_EXPORT_DIR resolved (the shipped default always resolves) this
        # dialog held qa_submit_suite -- a FINISHED, already-persisted suite
        # -- open on a client that may never answer, and the retried submit
        # then produced a second suite. An empty export_dir is the legacy
        # secure-temp install, where the tester's answer is the only signal.
        return await _auto_export_ask_dir(request.ask_text, request.progress)
    return export_dir, ""


# Best-effort Excel auto-export for QA_AUTO_EXPORT_XLSX (MCP path only).
#
# Reuses generate_test_case_xlsx so the cell_sanitizer formula-injection
# protection applies identically. When elicitation dialogs are available
# (QA_MCP_ELICIT_ENABLED + a capable client) the tester is first asked
# where to save the file; a declined/blank/unavailable answer keeps the
# configured default. When settings.qa_export_dir is set the file
# lands in that stable folder under the qa_test_cases_*.xlsx naming so a
# non-technical tester can find and re-open a persistent deliverable;
# otherwise it keeps the legacy secure-temp behavior via the
# _EXPORTERS["xlsx"] path exactly as qa_export_suite uses it. NEVER raises: an
# unusable directory falls back to temp, and any exporter error returns a
# warning note instead of breaking the already-generated (and persisted)
# suite result.
#
# The exporter is called via the module global (direct call for the
# explicit-path branch, the _EXPORTERS["xlsx"] lambda for the temp branch),
# so monkeypatch.setattr(mcp_handlers, "generate_test_case_xlsx", ...)
# intercepts BOTH branches at call time.
async def _auto_export_xlsx(suite, request: AutoExportRequest | None = None) -> str:
    """Best-effort Excel auto-export for QA_AUTO_EXPORT_XLSX (MCP path only)."""
    request = request or AutoExportRequest()
    try:
        await _common.emit(request.progress, "📄 Writing the Excel export…")
        export_dir, reject_note = await _export_destination(request)
        output_path = None
        if export_dir:
            output_path = _auto_export_target(suite, export_dir, request.source_url)
        path = await _auto_export_write(suite, output_path, request.on_path)
        return await _auto_export_reply(path, reject_note)
    except Exception as exc:
        logger.exception("mcp auto-export xlsx failed")
        return (
            f"⚠️ Auto-export to Excel failed: {exc} — you can still "
            "export the suite with `qa_export_suite`."
        )
