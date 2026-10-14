"""Reply-text ports of the host submit pipeline (C3a step 0).

Sibling handler modules reach the pipeline only through these, read at call
time, so a test that patches a module attribute still bites after the move.
"""

from __future__ import annotations

from typing import Any, Protocol

from agents.host_submit_ports_io import AFn, Fn


class CommonPort(Protocol):
    """Audit, progress, error capture and build flags (``handlers._common``)."""

    audit: AFn
    emit: AFn
    capture_error: Fn
    boot_version: Fn
    test_cases_only: Fn


class ElicitPort(Protocol):
    """Provenance and recent-suite helpers (``handlers._elicit``)."""

    unrecognized_client_note: Fn
    union_provenance: Fn
    dispatch_provenance: Fn
    recent_suites_markdown: AFn


class ReplyPort(Protocol):
    """Reply shaping and the pinned section priorities (``handlers._reply``)."""

    ReplySection: Fn
    GenerationReply: Fn
    shape_generation_result: Fn
    summary_budget: Fn
    assemble_finalize_reply: Fn
    suite_unreadable_reply: Fn
    oversized_full_suite_note: Fn
    P_REPORT: Any
    P_PROVENANCE: Any
    P_POINTER: Any
    P_EXPORTED: Any


class OutPort(Protocol):
    """Export and corpus side effects (``handlers.export`` / ``handlers.generate``)."""

    auto_export_xlsx: AFn
    persist_suite_to_corpus: AFn
    duplicate_case_note: AFn


class CategoryPort(Protocol):
    """Gates and notes shared with qa_submit_category (``handlers.submit_category``)."""

    staged_resubmit_hint: Fn
    volume_floor_gate: AFn
    step_assertion_gate: AFn
    step_assertion_gen_notes: Fn
    staged_row_count: Fn
    quality_advisory_gen_notes: Fn
    oversized_submission_note: Fn
    early_gen_notes: AFn
    checklist_gap_gen_notes: Fn
    ambiguity_gen_notes: Fn
