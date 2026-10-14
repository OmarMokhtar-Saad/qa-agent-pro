"""Duplicate-review stage of qa_submit_suite (moved from handlers, C3a step 2d).

The host's OPTIONAL cross-category duplicate review rode in on THIS submission.
Its SHAPE was validated against the submitted tc_ids inside host_mode; shape
validation is not a safety bound, so screen_duplicate_groups runs next in BOTH
modes and every refusal is disclosed. Removal additionally needs
QA_HOST_DEDUP_APPLY and MUST happen BEFORE finalize_generation, because that
renumbers every tc_id. The agreement score is advisory and never a veto.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from agents.host_submit_dup_notes import StatusIn, status_note
from agents.host_submit_ports_prep import SubmitPorts
from agents.host_submit_types import SubDup


class DupIn(NamedTuple):
    parsed: Any
    has_full: bool
    all_cases: list


def _screen_apply(
    cases: list, groups: list, ports: SubmitPorts
) -> tuple[list, list, list]:
    """(cases, removed, notes) after screening and applying the host's groups."""
    screened, notes = ports.media.screen_duplicate_groups(cases, groups)
    notes = list(notes)
    removed: list = []
    if screened:
        cases, removed, apply_notes = ports.media.apply_duplicate_groups(
            cases, screened
        )
        notes += apply_notes
    return cases, removed, notes


def review(args: DupIn, ports: SubmitPorts) -> SubDup:
    apply = bool(ports.io.settings.qa_host_dedup_apply)
    groups = list(getattr(args.parsed, "duplicate_groups", None) or [])
    notes = list(getattr(args.parsed, "duplicate_notes", None) or [])
    cases = args.all_cases
    removed: list = []
    agreements: list = []
    status = status_note(StatusIn(args.parsed, args.has_full, groups, True))
    if groups:
        agreements = ports.media.host_dedup_dup_agreements(cases, groups)
        if apply:
            cases, removed, screen_notes = _screen_apply(cases, groups, ports)
            notes += screen_notes
    return SubDup(
        cases, True, apply, groups, notes, len(groups), removed, "", agreements, status
    )
