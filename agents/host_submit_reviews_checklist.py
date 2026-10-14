"""Requirement-checklist review of qa_submit_suite (moved from handlers, C3a step 2b).

F6 (2026-08-15): "there is no note" and "the note says no checklist arrived"
are different states, and only the second is a capability loss worth hoisting
and recording. Tracked explicitly rather than sniffed out of the rendered
markdown.

CARRIED FORWARD (residue R4, review iteration 3): what this prep already
holds from an earlier submit of the SAME prep_id. The host is not told to
resend `checklist_items` on a resubmit and reasonably does not, so without
this a resubmit would finalize with no requirement checklist at all. Keep the
carried list EXACTLY as it is (a resubmission must not silently re-scope the
requirement set) and reuse its stored audit, so the granularity score is the
one that was computed over this very list. A carried-forward list is NOT a
gap: the requirements are still in force and the traceability report still
reads them.
"""

from __future__ import annotations

from typing import Any

from agents.host_submit_ports_prep import SubmitPorts
from agents.host_submit_types import ReviewIn, SubChecklist
from tools import atomic_checklist


def _adopt(prepared: Any, result: Any) -> dict:
    audit = atomic_checklist.audit_granularity(result.items)
    prepared.checklist_items = list(result.items)
    prepared.checklist_presented_ids = [it.item_id for it in result.items]
    prepared.checklist_audit = audit
    return audit


def checklist_review(args: ReviewIn, ports: SubmitPorts) -> SubChecklist:
    prepared = args.prepared
    if not args.meta.get("host_checklist_job"):
        return SubChecklist("", False)
    raw = getattr(args.parsed, "raw_checklist_items", None)
    result = ports.review.extract_host_checklist(raw)
    audit: dict = {}
    carried = len(list(getattr(prepared, "checklist_items", None) or []))
    if result.ran:
        audit = _adopt(prepared, result)
    elif carried:
        audit = dict(getattr(prepared, "checklist_audit", None) or {})
    note = ports.review.build_host_checklist_section(
        result, audit, carried=(0 if result.ran else carried)
    )
    return SubChecklist(note, not result.ran and not carried)
