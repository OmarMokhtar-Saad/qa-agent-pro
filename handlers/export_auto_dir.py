"""Where the auto-export workbook goes: the save-folder question and the target path.

Moved out of handlers.export.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import cast

from handlers import _common, _elicit
from handlers.export_dir_notes import _TEMP_EXPORT_LABEL
from handlers.export_dir_rules import _safe_elicited_dir
from handlers.export_paths import _export_ticket_slug

logger = logging.getLogger("tools.mcp_handlers")


def _timeout_note() -> str:
    logger.warning(
        "auto-export: no save-folder answer within %ss -- using the "
        "default export folder",
        int(_elicit.elicit_timeout_s()),
    )
    return (
        "\n> ℹ️  No answer to the save-folder question arrived "
        f"within {int(_elicit.elicit_timeout_s())}s, so the default export "
        "folder was used."
    )


def _budget_note() -> str:
    logger.warning(
        "auto-export: save-folder dialog skipped (per-call "
        "elicitation budget spent) -- using the default folder"
    )
    return (
        "\n> ℹ️  This call had already run past its interactive "
        "budget, so the save-folder question was not asked and the "
        "default export folder was used."
    )


# The two causes get DIFFERENT sentences. Saying "no answer arrived
# within 55s" when no dialog was ever shown -- which is what a spent
# budget means -- would be untrue, and auto_export_xlsx is reached
# at the very end of a full generation, so that is the likely case
# on the generate path rather than an exotic one.
def _unanswered_dir_note(asked) -> str:
    """The note for a save-folder question that got no answer, per cause."""
    return _timeout_note() if asked.timed_out else _budget_note()


async def _ask_folder(ask_text: _elicit.AskCb):
    # K1 (2026-08-10): the outer asyncio.wait_for F1b added here is gone --
    # _elicit_text bounds itself now, so wrapping it again would just be a
    # second, looser bound on the same await.
    #
    # "leave blank" was a LIE: Cursor renders the dialog with a REQUIRED
    # Value* field and will not submit an empty answer, so the advertised
    # optional path was unreachable. The word `default` is reachable, and
    # _safe_elicited_dir(sentinel_ok=True) understands it.
    return await _elicit._elicit_text(
        ask_text,
        "Where should the Excel file be saved? Reply with a full folder "
        "path, or reply `default` to use the default (a secure temp "
        f"folder). No answer within {int(_elicit.elicit_timeout_s())}s also uses "
        "the default.",
    )


# ops-6 (bug 3): UNTRUSTED host text -- validate before it
# becomes a real directory. A rejected answer keeps the
# configured default AND says so.
# export_dir is EMPTY in this branch (the dialog is only
# asked when the configured folder did not resolve), so a
# rejected answer falls back to the secure temp dir -- and
# the note has to name that one, not a configured folder.
def _accept_answer(value, export_dir: str, reject_note: str) -> tuple[str, str]:
    picked, why = _safe_elicited_dir(
        cast("str", value),
        sentinel_ok=True,
        fallback_label=_TEMP_EXPORT_LABEL,
    )
    if picked:
        return picked, reject_note
    return export_dir, why or reject_note


async def _auto_export_ask_dir(
    ask_text: _elicit.AskCb, progress: _common.ProgressCb
) -> tuple[str, str]:
    """Ask the tester where to save; returns (export_dir, reject_note)."""
    await _common.emit(progress, "📂 Asking where to save the Excel file…")
    asked = await _ask_folder(ask_text)
    reject_note = _unanswered_dir_note(asked) if _common.unanswered(asked) else ""
    if asked.status == _common.CHOSEN and (asked.value or "").strip():
        return _accept_answer(asked.value, "", reject_note)
    return "", reject_note


def _workbook_name(suite, source_url: str) -> str:
    frag = (getattr(suite, "suite_id", "") or "suite")[:16]
    stamp = time.strftime("%Y%m%d_%H%M%S")
    # F11: ticket key FIRST, because that is what a tester scans a
    # folder for. The `qa_test_cases_` prefix is kept (every
    # cleanup_temp_files glob keys off it) and so are the suite_id
    # fragment and the timestamp, so uniqueness and every existing
    # consumer are unchanged. "" for a non-Jira source -> the
    # previous name exactly.
    ticket = _export_ticket_slug(source_url)
    if ticket:
        return f"qa_test_cases_{ticket}_{frag}_{stamp}.xlsx"
    return f"qa_test_cases_{frag}_{stamp}.xlsx"


def _auto_export_target(suite, export_dir: str, source_url: str) -> str | None:
    """The workbook path inside export_dir, or None when it is unusable."""
    try:
        dest = Path(export_dir).expanduser()
        dest.mkdir(parents=True, exist_ok=True)
        return str((dest / _workbook_name(suite, source_url)).resolve())
    except OSError:
        logger.warning(
            "qa_export_dir %r unusable -- falling back to temp export",
            export_dir,
            exc_info=True,
        )
        return None
