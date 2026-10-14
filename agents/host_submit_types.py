"""Typed carriers shared by the host submit pipeline (public names; C3a step 0).

One NamedTuple per stage result, plus the push request. They mirror the
handler's former private ``_Sub*`` tuples field for field.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NamedTuple


class SubReq(NamedTuple):
    """Identity of one submit call: the prep plus what the host sent."""

    prep_id: str
    envelope: dict
    meta: dict
    suite_json: Any = None
    has_full: bool = False


class SubPrep(NamedTuple):
    prep_id: str
    envelope: dict
    prepared: Any
    meta: dict
    source_text: str
    source_url: Any
    version_note: str


class SubRoute(NamedTuple):
    sidecar_obj: Any
    sidecar_raw: Any
    superseded_note: str


class SubRead(NamedTuple):
    parsed: Any
    has_full: bool
    conflict_note: str
    rows: Any


class SubCases(NamedTuple):
    all_cases: list
    staged_rows: Any
    submitted_cases: list
    cat_source: str


class SubGates(NamedTuple):
    volume_note: str
    assertion_note: str
    assert_findings: Any
    data_findings: Any
    dropped_note: str


class SubAmb(NamedTuple):
    result: Any
    note: str


class ReviewIn(NamedTuple):
    """What the ac/grounding/checklist reviews read from one submission."""

    meta: dict
    parsed: Any
    prepared: Any
    all_cases: list


class SubAc(NamedTuple):
    result: Any
    note: str


class SubGround(NamedTuple):
    note: str
    assumed_rows: list
    all_cases: list


class SubChecklist(NamedTuple):
    note: str
    gap: bool


class SubAcks(NamedTuple):
    volume_floor: bool
    image_relevance: bool
    step_assertion: bool
    quality_gate: bool


class SubImages(NamedTuple):
    note: str
    result: Any
    counts: dict


class SubDup(NamedTuple):
    all_cases: list
    review_on: bool
    apply: bool
    groups: list
    notes: list
    groups_submitted: int
    removed: list
    note: str
    agreements: list
    status_note: str


class SubRun(NamedTuple):
    prep: SubPrep
    read: SubRead
    cases: SubCases
    gates: SubGates
    amb: SubAmb
    ac: SubAc
    ground: SubGround
    checklist: SubChecklist
    images: SubImages
    dup: SubDup


class SubFinal(NamedTuple):
    suite: Any
    summary: Any
    status: Any
    fa_skip_note: str


class SubQuality(NamedTuple):
    note: str


class SubPost(NamedTuple):
    fin: SubFinal
    volume_note: str
    quality_note: str
    dup_note: str


class SubProv(NamedTuple):
    dup_case_note: str
    dup_counts: Any
    provenance: Any
    session_note: str


class SubSaved(NamedTuple):
    suite_id: str
    persist_note: str
    prov: SubProv


class SubExport(NamedTuple):
    note: str
    paths: list
    auto_export: bool


class SubTail(NamedTuple):
    prescreen_head: str
    prescreen_pairs: str
    rtm_note: str
    cov_signal_note: str


@dataclass(frozen=True)
class PushRequest:
    """What one qa_push_suite call asks for, after normalisation."""

    suite_id: str
    target: str
    apply: bool
    project_id: Any = 0
    section_name: str = ""
