"""qa-doctor's export-directory row (moved from setup_check)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from handlers import export

logger = logging.getLogger("tools.mcp_handlers")


def _write_probe_ok(target: Path) -> bool:
    export_ok = False
    _probe_file = target / f".qa-write-probe-{os.getpid()}"
    try:
        _probe_file.write_text("", encoding="utf-8")
        export_ok = True
    except OSError:
        export_ok = False
    finally:
        try:
            _probe_file.unlink()
        except OSError:
            logger.debug("export write-probe cleanup failed", exc_info=True)
    return export_ok


def _parent_writable(target: Path) -> bool:
    _parent = target
    while not _parent.exists() and _parent.parent != _parent:
        _parent = _parent.parent
    return os.access(_parent, os.W_OK)


# A real write, not os.access on whichever ancestor happens to
# exist: that answered a different question ('is some parent
# writable') and printed a green tick for it. os.access also
# consults permission bits only, which is the wrong answer on a
# read-only mount or an ACL-governed share.
# Not created yet: the honest claim is about the parent, and the
# label says so rather than implying the folder is ready.
def _export_write_probe(target: Path) -> tuple[bool, bool]:
    """Probe the export folder (or its nearest parent) for writability."""
    export_exists = target.is_dir()
    if export_exists:
        return export_exists, _write_probe_ok(target)
    return export_exists, _parent_writable(target)


def _export_status_suffix(export_ok: bool, export_exists: bool) -> str:
    """Return the status clause appended to the export-directory row."""
    return (
        (
            " — checked by writing to it just now"
            if export_exists
            else " — the folder does not exist yet and will be "
            "created on the first export; its parent is writable"
        )
        if export_ok
        else (
            " — not writable: a test file could not be created there just now"
            if export_exists
            else " — not writable: the folder does not exist and "
            "its parent cannot be written to either"
        )
    )


def _export_line(dest: Path, export_ok: bool, export_exists: bool) -> str:
    return (
        f"- {'✅' if export_ok else '⚠️'} **Excel auto-export** — "
        f"files are saved to `{dest}` (set `QA_EXPORT_DIR`, or pass "
        "`output_dir` to `qa_export_suite`)"
        + _export_status_suffix(export_ok, export_exists)
    )


# K5 (2026-08-10): this said "you choose where each file is
# saved" -- but F1a gates the save-folder dialog on an
# UNRESOLVED export dir, so in THIS branch the tester is never
# asked. QA_EXPORT_DIR is the control here, and
# qa_export_suite(output_dir=...) is the per-call override.
def _export_dir_line(recommended: list[str]) -> str:
    """Build the Excel auto-export row; queue a fix when it is not writable."""
    export_dir = export.resolved_export_dir()
    if not export_dir:
        return (
            "- ✅ **Excel auto-export** — you choose where each file "
            "is saved (fallback: secure temp directory)"
        )
    dest = Path(export_dir).expanduser()
    target = dest if dest.is_absolute() else Path.cwd() / dest
    export_exists, export_ok = _export_write_probe(target)
    export_line = _export_line(dest, export_ok, export_exists)
    if not export_ok:
        recommended.append(
            f"Make the export directory `{dest}` writable (or "
            "change QA_EXPORT_DIR) — until then generated Excel "
            "files fall back to a temp folder."
        )
    return export_line
