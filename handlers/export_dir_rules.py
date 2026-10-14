"""Validation of an elicited save-folder answer (moved out of handlers.export)."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import tools.updater as updater
from config.settings_guard import never_raise
from handlers.export_dir_notes import (
    _TEMP_EXPORT_LABEL,
    _config_line_note,
    _install_itself_note,
    _not_full_path_note,
    _outside_roots_note,
    _too_long_note,
    _unusable_note,
)
from tools.process_env import home_dir

logger = logging.getLogger("tools.mcp_handlers")

# ops-6 (bug 3): cap on an elicited folder answer before it is treated as a path.
_MAX_EXPORT_DIR_CHARS = 400


# K3 (2026-08-10): Cursor will not submit an EMPTY elicitation value, so
# the prompt's old "leave blank for the default" was unreachable and the
# prompt now offers the word `default` instead. Without this branch that
# word falls through to the not-a-full-path rejection below and the tester
# is told their answer "is not a full path" -- true, but a non-sequitur for
# an answer the prompt asked them to give. Returning ("", "") is the
# keep-the-configured-default path, silently, which is what they asked for.
# sentinel_ok is opt-in so qa_export_suite(output_dir="default") -- a real
# parameter, never prompted -- keeps its corrective note.
def _screen_elicited_dir(
    raw: str, sentinel_ok: bool, fallback_label: str
) -> tuple[str, str] | None:
    """Text-only checks of `_safe_elicited_dir`; None means "go on to the path checks"."""
    if sentinel_ok and raw.lower() == "default":
        return "", ""
    if len(raw) > _MAX_EXPORT_DIR_CHARS:
        return "", _too_long_note(fallback_label)
    if any(ch in raw for ch in ("#", "\n", "\r", "\x00")):
        return "", _config_line_note(fallback_label)
    return None


def _hidden_folder_note(resolved: Path, roots: list[Path], fallback_label: str) -> str:
    """Rejection note when *resolved* is in a dot-directory under a root, else ''."""
    # A dot-directory (~/.ssh, ~/.aws, ~/.config) is no place for a deliverable.
    for root in roots:
        if root in resolved.parents and any(
            part.startswith(".") for part in resolved.relative_to(root).parts
        ):
            return (
                "\n> ℹ️  The folder you replied with is inside a hidden "
                f"folder, so {fallback_label} was used instead."
            )
    return ""


def _allowed_roots() -> list[Path]:
    """The install dir, the home folder and the temp folder, resolved, in that order."""

    return [
        Path(updater._INSTALL_DIR).resolve(),
        home_dir().resolve(),
        Path(tempfile.gettempdir()).resolve(),
    ]


# F2 (2026-08-10): an ELICITED answer is free-text a tester typed, so a
# bare word like "desktop" must never be resolved against the process
# CWD -- that is the install dir, which is itself an allowed root, so the
# check below APPROVED it and the deliverable landed inside the
# installation. Only absolute / ~-rooted answers reach that check.
def _resolve_elicited_dir(raw: str, fallback_label: str) -> tuple[str, str]:
    """Path half of `_safe_elicited_dir`: absolute check, then the allowed roots.

    May raise; the caller turns any failure into a rejection note."""
    if not Path(raw).expanduser().is_absolute():
        return "", _not_full_path_note(raw, fallback_label)

    resolved = Path(raw).expanduser().resolve()
    roots = _allowed_roots()
    if not any(resolved == r or r in resolved.parents for r in roots):
        return "", _outside_roots_note(fallback_label)
    install = roots[0]
    if resolved == install:
        return "", _install_itself_note(fallback_label)
    if install not in resolved.parents:
        hidden = _hidden_folder_note(resolved, roots[1:], fallback_label)
        if hidden:
            return "", hidden
    return str(resolved), ""


# Validate an elicited save-folder answer BEFORE it becomes a real path.
#
# Returns ``(directory_or_empty, note)``. An empty directory means "rejected --
# keep the default destination", and *note* is always non-empty in that case so
# the tester is told, rather than silently getting a different location.
#
# *fallback_label* NAMES that destination inside every rejection note: only the
# caller knows where its own default write lands, and a note that claims a
# folder the file did not go to is worse than no note at all -- the tester goes
# looking in an empty directory. The reply prints the real path beside it.
#
# WHY: the answer is UNTRUSTED host-model text, and it used to be passed
# straight to ``Path(...).mkdir(parents=True)``. On 2026-07-29 a host echoed
# back the raw `.env` line -- inline comment included -- and the server created
# ``data/exports       # save auto-exported .xlsx here (... sessions/`` plus an
# ``updates)`` subdirectory, splitting on the `/` inside the comment, then
# reported that path to the tester as the location of their file.
#
# Rules: no comment/newline/NUL characters (a comment marker means the answer
# is a config line, not a folder), a length cap, and the resolved path must sit
# under the install dir, the user's home, or the system temp dir -- so an
# answer like ``~/../../etc`` cannot direct writes anywhere it likes. Never
# raises: any failure rejects the answer and keeps the default.
def _safe_elicited_dir(
    answer: str,
    sentinel_ok: bool = False,
    fallback_label: str = _TEMP_EXPORT_LABEL,
) -> tuple[str, str]:
    """Validate an elicited save-folder answer BEFORE it becomes a real path."""
    raw = (answer or "").strip()
    if not raw:
        return "", ""
    screened = _screen_elicited_dir(raw, sentinel_ok, fallback_label)
    if screened is not None:
        return screened
    with never_raise(logger, "elicited export dir rejected", exc_info=True):
        return _resolve_elicited_dir(raw, fallback_label)
    return "", _unusable_note(fallback_label)
