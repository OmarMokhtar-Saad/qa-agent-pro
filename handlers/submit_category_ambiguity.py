"""The ambiguity-preflight generation notes: did not run, or names inferred."""

from __future__ import annotations

from typing import Any

from agents.host_jobs import ambiguity_notes_gen_note

_PREFLIGHT_NOT_RUN = (
    "Ambiguity preflight did not run",
    "The testability pre-pass was "
    "declared `blocking: true` in the prepare payload, but "
    "it runs inside the tester's chat model and this "
    "submission came back with no readable result -- so "
    "there is no evidence it ran, and this server cannot "
    "enforce a step it does not execute. These cases were "
    "generated against a ticket nothing checked for being "
    "too under-specified to test. That is NOT the same as "
    "'checked and found nothing'.",
)


def _inferred_names_note(severity: str) -> tuple:
    """The note that a medium/high source means element names are inferred."""
    return (
        "UI element names may be inferred",
        "The testability preflight rated this source "
        f"`{severity}`. Where the source names no "
        "screen, no control and no label, the screen names, "
        "element names and UI copy in these cases were "
        "inferred by the generating model, not promised by "
        "the ticket. Confirm an element exists before "
        "raising a defect for its absence, and treat a "
        "mismatch in wording as a question for the BA rather "
        "than a bug.",
    )


def ambiguity_gen_notes(amb_result: Any) -> list:
    """Generation notes from the ambiguity preflight (the workbook is what the tester keeps).

    It did not run, it rated the source medium/high (element names are
    inferred), and any unreadable field recorded in ``result.notes`` (the chat
    reply is transient)."""
    notes: list = []
    ran = getattr(amb_result, "ran", False)
    if amb_result is not None and not ran:
        notes.append(_PREFLIGHT_NOT_RUN)
    if amb_result is not None and ran:
        if getattr(amb_result, "severity", "") in ("medium", "high"):
            notes.append(_inferred_names_note(amb_result.severity))
    amb_note = ambiguity_notes_gen_note(amb_result)
    if amb_note is not None:
        notes.append(amb_note)
    return notes
