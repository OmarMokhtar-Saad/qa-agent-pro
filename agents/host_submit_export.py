"""Excel export and finalize stamp of qa_submit_suite (moved from handlers, C3a step 3c).

F03: result_md is shaped at the RETURN, not here -- its summary budget depends
on export_note, rtm_note and cov_signal_note, none of which exist yet.
export_note is FRONT-loaded (see handlers.export.auto_export_xlsx), near the
HEAD of the reply, so a paraphrasing host model cannot drop the deliverable.
Exactly ONE thing outranks it: the ambiguity-screening verdict (2026-08-09,
Batch 3 FIX 2), because a path for a suite with NO ambiguity screening is the
over-claim. ``amb_note`` is "" for a prep that shipped no ambiguity job.

Fix 5b: STAMP instead of DELETE. Deleting made "prep gone" mean three things,
so a resubmit after a SUCCESSFUL finalize was told to re-prepare. Only the
success path is stamped; on a stamp error fall back to deleting, because an
unstamped prep would be offered as resumable.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, NamedTuple

from agents.host_submit_ports_prep import SubmitPorts
from agents.host_submit_types import SubPrep
from tools import prep_store

logger = logging.getLogger("agents.host_mode")


class AutoExportRequest(NamedTuple):
    """The optional inputs of the auto-export port; every field defaults to "none"."""

    ask_text: Any = None
    on_path: Callable[[str], None] | None = None
    progress: Any = None
    source_url: str = ""


class SubExport(NamedTuple):
    note: str
    paths: list
    auto_export: bool


class ExportIn(NamedTuple):
    prep: SubPrep
    suite: Any
    ask_text: Any
    progress: Any


async def export_submitted_suite(args: ExportIn, ports: SubmitPorts) -> SubExport:
    """Auto-export the xlsx; the note carries its own trailing separator."""
    suite, prep = args.suite, args.prep
    auto_export = bool(getattr(suite, "test_cases", None))
    xlsx_paths: list[str] = []
    export_note = ""
    if auto_export:
        request = AutoExportRequest(
            ask_text=args.ask_text,
            on_path=xlsx_paths.append,
            progress=args.progress,
            source_url=prep.source_url or prep.source_text,
        )
        export_note = await ports.out.auto_export_xlsx(suite, request)
        if export_note:
            export_note += "\n\n---\n\n"
    return SubExport(export_note, xlsx_paths, auto_export)


async def stamp_prep_finalized(prep: SubPrep, suite: Any, xlsx_paths: list) -> None:
    prep_id = prep.prep_id
    envelope = prep.envelope
    _final_stamp = {
        "suite_id": str(getattr(suite, "suite_id", "") or ""),
        "export_path": str(xlsx_paths[0]) if xlsx_paths else "",
    }
    _stamped = await prep_store.update_prep(
        prep_id, {**envelope, prep_store.FINALIZED_KEY: _final_stamp}
    )
    if _stamped.get("error"):
        logger.warning(
            "prep %s could not be stamped finalized (%s) - deleting instead",
            prep_id,
            _stamped.get("error"),
        )
        await prep_store.delete_prep(prep_id)
