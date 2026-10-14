"""The Generation Notes sheet entries a finalize collects before export."""

from __future__ import annotations

import logging
import sys

from agents.host_category_calls import EarlyNotesIn
from config.settings_guard import never_raise
from handlers import _elicit, _reply, generate, generate_prepare, prep_status
from handlers.seam_alias import alias
from tools import step_assertion
from tools.suite_quality_signals import (
    AdvisoryContext,
    compute_suite_quality_advisories,
)

logger = logging.getLogger("tools.mcp_handlers")


def _skipped_attachments_gen_notes(meta: dict) -> list:
    """Generation note: attachments the tester's own text-only pick skipped."""
    with never_raise(logger, "skipped-attachments gen note failed", exc_info=True):
        note = generate_prepare.text_only_image_skip_note(meta)
        return [("Skipped attachments", note)] if note else []
    return []


async def _recent_suite_gen_notes(source_text: str) -> list:
    """Generation note: a suite for this same source was saved recently."""
    with never_raise(logger, "recent-suite gen note failed", exc_info=True):
        note = await generate.gen_recent_suite_warning_note(
            source_text, generate._RECENT_SUITE_WARN_WINDOW_S
        )
        return [("Recent suite for this source", note)] if note else []
    return []


# A1 (2026-09-26): signals that already exist server-side but never reached the
# Generation Notes sheet, up to the volume shortfall. Each is independent and
# best-effort; the order is the sheet's order.
async def early_gen_notes(args: EarlyNotesIn) -> list:
    meta, suite = args.meta, args.suite
    notes = _skipped_attachments_gen_notes(meta)
    if args.dup_note:
        notes.append(("Duplicate cases", args.dup_note))
    notes += await _recent_suite_gen_notes(args.source_text)
    provenance_detail = _elicit._provenance_gen_note_detail(args.provenance)
    if provenance_detail:
        notes.append(("Session provenance", provenance_detail))
    screen_ref = str(meta.get("image_reference_advisory") or "")
    if screen_ref:
        notes.append(("Screen references without attachments", screen_ref))
    vol_detail = prep_status.volume_shortfall_detail(
        meta, list(getattr(suite, "test_cases", None) or [])
    )
    if vol_detail:
        notes.append(("Under-generated categories", vol_detail))
    return notes


def checklist_gap_gen_notes(checklist_gap: bool) -> list:
    """Generation note: the run returned no usable requirements checklist."""
    if not checklist_gap:
        return []
    return [
        (
            "No requirements checklist",
            "The requirement decomposition runs in the tester's chat "
            "model, not on this server, and this run returned no "
            "usable `checklist_items`. There is therefore NO "
            "requirements checklist for this suite, and no "
            "Requirements Checklist sheet. "
            "Nothing was invented to fill the gap -- treat the "
            "suite's coverage as unmeasured, not as complete.",
        )
    ]


def step_assertion_gen_notes(findings: list, data_findings: list) -> list:
    """F4 (2026-08-29): advisory notes in the workbook the tester keeps. The
    REFUSAL is the gate; the test_data finding never gates at all."""
    notes: list = []
    assert_detail = step_assertion.step_assertion_detail(findings)
    if assert_detail:
        notes.append(("Steps that cannot fail", assert_detail))
    data_detail = step_assertion.test_data_detail(data_findings)
    if data_detail:
        notes.append(("Test data that restates the field name", data_detail))
    return notes


def _quality_notes(all_cases: list, source_text: str) -> list:
    """C1 (2026-09-26) suite-quality advisories; best-effort, never blocks export."""
    notes: list = []
    with never_raise(logger, "compute_suite_quality_advisories failed", exc_info=True):
        for title, detail in compute_suite_quality_advisories(
            all_cases, AdvisoryContext(staged_text=source_text)
        ):
            notes.append((title, detail))
    return notes


def quality_advisory_gen_notes(all_cases: list, source_text: str, summary: str) -> list:
    """Suite-quality advisories, then F8 (2026-08-30) the FULL generation advisory.

    The full advisory is added when the chat reply cut it at ``_SUMMARY_CAP``.
    Both are best-effort: a note must never cost the tester the export."""
    notes = _quality_notes(all_cases, source_text)
    with never_raise(logger, "full-advisory note attachment failed", exc_info=True):
        full_summary = (summary or "").strip()
        if len(full_summary) > _reply._SUMMARY_CAP:
            notes.append(
                ("Full generation advisory (the chat reply was cut)", full_summary)
            )
    return notes


_early_gen_notes = alias(sys.modules[__name__], "early_gen_notes")
