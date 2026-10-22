"""Writing one loaded suite in one format (moved out of handlers.export)."""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any, NamedTuple

from handlers import _common, _reply
from handlers.export_dir_notes import _TEMP_EXPORT_LABEL
from handlers.export_dir_rules import _safe_elicited_dir
from handlers.export_formats import _available_exporters
from tools import telemetry

logger = logging.getLogger("tools.mcp_handlers")


class ExportOptions(NamedTuple):
    """The optional inputs of `handle_export_suite`; every field defaults to "none"."""

    output_dir: str = ""
    prep_id: str = ""
    choose: _common.ChooseCb = None
    progress: _common.ProgressCb = None


class ExportJob(NamedTuple):
    """One export in flight: the loaded suite and where its reply goes."""

    suite: Any
    suite_id: str
    fmt: str
    output_dir: str
    progress: _common.ProgressCb


class _Placed(NamedTuple):
    """Where the written file ended up, the note to append, and custom-folder flag."""

    path: str
    note: str
    custom: bool


_MOVE_FAILED_NOTE = (
    "\n> ℹ️  I could not move the file into the folder you asked "
    "for, so it stayed at the path above."
)


# Move a written export into the folder the tester named.
#
# Returns ``(path, note)``. The note is non-empty ONLY when the move failed,
# and in that case the ORIGINAL path comes back, so the reply always points at
# a file that exists. Never raises.
#
# WHY a move rather than an output path: the five single-file exporters each
# choose their own filename and extension, so threading a directory into them
# would mean a per-format extension table that can silently drift. The Zephyr
# exporter was the one exception -- it wrote a PAIR into a directory and took
# the folder directly -- and it was DELETED on 2026-08-15 (dead-code
# deletion batch D4), so every surviving format is relocated by this function.
def _free_name(dest: Path) -> Path:
    """``dest``, or ``name (2).ext`` etc. so an existing file is never overwritten."""
    n = 2
    cand = dest
    while cand.exists():
        cand = dest.with_name(f"{dest.stem} ({n}){dest.suffix}")
        n += 1
    return cand


def _relocate_export(path: str, target_dir: str) -> tuple[str, str]:
    """Move a written export into the folder the tester named."""
    try:
        src = Path(path)
        dest_dir = Path(target_dir).expanduser()
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        if src.resolve() == dest.resolve():
            return str(dest), ""
        dest = _free_name(dest)
        shutil.move(str(src), str(dest))
        return str(dest), ""
    except (OSError, RuntimeError, ValueError, TypeError):
        logger.warning(
            "export relocate to %r failed -- keeping the original path",
            target_dir,
            exc_info=True,
        )
        return path, _MOVE_FAILED_NOTE


# I4 (2026-08-10): UNTRUSTED folder text, validated by exactly the same
# rules as an elicited answer -- absolute / ~-rooted only, no config
# lines, length-capped, root-allowlisted, with the "did you mean
# ~/Desktop?" correction for a bare well-known word. Every branch below
# keys off `target_dir`, the RESOLVED value: a rejected answer is
# truthy as raw text but resolves to "", and must not send the file
# anywhere. A rejection keeps the default destination AND says so.
def _checked_target(output_dir: str) -> tuple[str, str]:
    """The resolved save folder and the note for a rejected one."""
    if not (output_dir or "").strip():
        return "", ""
    target_dir, why = _safe_elicited_dir(output_dir, fallback_label=_TEMP_EXPORT_LABEL)
    return target_dir, ("" if target_dir else why)


def _relocated(path: str, target_dir: str, dir_note: str) -> _Placed:
    """Relocate into *target_dir* when one was resolved; append any move note."""
    if target_dir:
        # Every surviving format writes ONE file, so relocating it is
        # safe. The exception was the Zephyr PAIR, which wrote itself
        # into target_dir and would have been split from its
        # zfj_import_config.json by a move; that exporter was DELETED
        # on 2026-08-15 (dead-code deletion batch D4).
        path, move_note = _relocate_export(path, target_dir)
        dir_note += move_note
    return _Placed(path, dir_note, bool(target_dir))


async def _export_done(job: ExportJob, placed: _Placed) -> str:
    """Telemetry, audit and the reply for a written export."""
    count = len(job.suite.test_cases)
    telemetry.add_tool_properties(format=job.fmt, case_count=count)
    await _common.audit(
        "mcp_export_suite",
        entity_id=job.suite_id,
        detail={"format": job.fmt, "path": placed.path, "custom_dir": placed.custom},
    )
    result = _reply.shape_export_result(
        _reply.ExportedFile(job.suite_id, job.fmt, placed.path, count)
    )
    if placed.note:
        result += placed.note
    return result


async def _export_suite_reply(job: ExportJob) -> str:
    """Write the loaded suite in *fmt*, relocate it if asked, shape the reply."""
    await _common.emit(job.progress, f"📦 Exporting suite to {job.fmt}…")
    target_dir, dir_note = _checked_target(job.output_dir)
    try:
        path = await asyncio.to_thread(_available_exporters()[job.fmt], job.suite)
    except Exception as exc:
        logger.exception("mcp export failed")
        return f"⚠️ Export to {job.fmt} failed: {exc}"
    placed = await asyncio.to_thread(_relocated, path, target_dir, dir_note)
    return await _export_done(job, placed)
