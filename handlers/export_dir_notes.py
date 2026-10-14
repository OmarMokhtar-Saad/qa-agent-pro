"""Rejection notes for a save-folder answer (moved out of handlers.export).

Each builder returns the exact text the tester reads; the callers decide when.
"""

from __future__ import annotations

# F2 (2026-08-10): the folder words a tester actually types. A bare relative
# answer is never resolved (it would land inside the install dir); the matching
# entry is offered back as the full path they meant.
_WELL_KNOWN_FOLDERS = {
    "desktop": "~/Desktop",
    "documents": "~/Documents",
    "downloads": "~/Downloads",
}


# The destination a REJECTED save-folder answer really falls back to. BOTH call
# sites land in the secure temp dir: auto_export_xlsx only ASKS when
# resolved_export_dir() is empty, and handle_export_suite's five single-file
# exporters each write into <tempdir>/qa_agents_exports/ when no directory is
# threaded into them.
#
# 2026-08-15 (dead-code deletion batch D4): this used to be wrapped in a
# _default_export_label(fmt) helper, because the Zephyr PAIR was the ONE export
# handed the configured directory and therefore the one format whose rejection
# note named a different folder. tools/zephyr_exporter.py is DELETED, so every
# surviving format falls back here and the helper was a constant-returning
# branch over nothing. Both call sites interpolate this name directly. If a
# format is ever added that writes somewhere else, bring the per-format helper
# back WITH a test -- do not quietly widen this constant.
_TEMP_EXPORT_LABEL = "a secure temp folder"


def _unusable_note(fallback_label: str) -> str:
    return (
        "\n> ℹ️  The folder you replied with could not be used, "
        f"so {fallback_label} was used instead."
    )


def _too_long_note(fallback_label: str) -> str:
    return (
        "\n> ℹ️  The folder you replied with was too long to be a real path, "
        f"so {fallback_label} was used instead."
    )


def _config_line_note(fallback_label: str) -> str:
    return (
        "\n> ℹ️  The folder you replied with looks like a config line rather "
        "than a folder (it contains a comment marker or a line break), "
        f"so {fallback_label} was used instead."
    )


def _not_full_path_note(raw: str, fallback_label: str) -> str:
    """Rejection note for an elicited answer that is not an absolute path."""
    suggestion = _WELL_KNOWN_FOLDERS.get(raw.lower())
    if suggestion:
        return (
            "\n> ℹ️  I need the FULL path of that folder, so "
            f"{fallback_label} was used instead. Reply "
            f"`{suggestion}` if that is the one you meant."
        )
    return (
        "\n> ℹ️  The folder you replied with is not a full path "
        "(it would have been read relative to the server's own folder), "
        f"so {fallback_label} was used instead."
    )


def _outside_roots_note(fallback_label: str) -> str:
    return (
        "\n> ℹ️  The folder you replied with is outside the install "
        "folder, your home folder and the temp folder, "
        f"so {fallback_label} was used instead."
    )


def _install_itself_note(fallback_label: str) -> str:
    return (
        "\n> ℹ️  The folder you replied with is the install folder itself, "
        f"so {fallback_label} was used instead."
    )
