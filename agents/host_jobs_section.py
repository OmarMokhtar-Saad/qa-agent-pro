"""Host mode: the ambiguity-result disclosure section and its workbook note (split from host_jobs.py)."""

from __future__ import annotations

import logging

from agents.host_jobs_blocks import (
    _ambiguity_high_block,
    _ambiguity_unverified_block,
)
from config.settings_guard import never_raise

logger = logging.getLogger("agents.host_mode")


def _ambiguity_ran_block(result, notes: list) -> str:
    """The block for a readable verdict: the `high` block, else the self-reported line."""
    sev = getattr(result, "severity", "") or "unknown"
    if sev == "high":
        return _ambiguity_high_block(result, notes)
    surface = getattr(result, "testable_surface", "") or "unspecified"
    out = [
        f"> \u2139\ufe0f  Ambiguity preflight: **{sev}** (testable surface: "
        f"{surface}) -- run by YOUR chat model, self-reported, and not "
        "verified by this server, which made no classifier call for it."
    ]
    out += [f">   - {n}" for n in notes]
    return "\n".join(out) + "\n\n"


def build_ambiguity_result_section(result) -> str:
    """The disclosure block for the boomeranged safety preflight.

    Emitted FIRST, ahead of every other section, because it is the one thing that
    can invalidate everything under it. "" when the job was never requested, so a
    server-classified run is byte-identical. Never raises.
    """
    with never_raise(logger, "build_ambiguity_result_section failed", exc_info=True):
        if result is None or not getattr(result, "requested", False):
            return ""
        notes = list(getattr(result, "notes", None) or [])
        if not getattr(result, "ran", False):
            return _ambiguity_unverified_block(notes)
        return _ambiguity_ran_block(result, notes)
    return ""


def ambiguity_notes_gen_note(result) -> tuple[str, str] | None:
    """(title, detail) for the Generation Notes workbook sheet when the
    boomeranged ambiguity_result carried any note at all (e.g. a rejected
    `testable_surface`) -- None otherwise. v1.97.0 cursor-hardening item 9:
    extract_ambiguity_result already records these notes and
    build_ambiguity_result_section already renders them into the CHAT REPLY;
    this is the workbook copy, the artifact the tester actually keeps. Mirrors
    that function's style exactly. Never raises.
    """
    with never_raise(logger, "ambiguity_notes_gen_note failed", exc_info=True):
        if result is None:
            return None
        notes = list(getattr(result, "notes", None) or [])
        if not notes:
            return None
        return ("Safety-preflight field(s) rejected", "; ".join(notes))
    return None
