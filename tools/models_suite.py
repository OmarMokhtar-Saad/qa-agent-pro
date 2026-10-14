from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, Field, PrivateAttr, field_validator

from tools.models_case import TestCase


class TestSuite(BaseModel):
    model_config = {"extra": "forbid"}

    suite_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        description="Auto-generated suite identifier — LEAVE EMPTY. Assigned by the "
        "system so the suite can be persisted, re-exported and pushed to a TMS by "
        "a stable key.",
    )
    test_cases: list[TestCase] = Field(
        min_length=1, description="All generated test cases"
    )

    # ``_report_artifacts`` (AC-Validation / Test Plan) was DELETED 2026-08-30
    # with tools/test_plan_report.py and the two XLSX report sheets: P2-H had
    # already removed its only writer on 2026-08-16, so it was permanently None.
    # The PrivateAttrs below are the surviving members of the same family and
    # carried their rationale by reference to it, which is why each now states
    # that rationale itself.

    # Atomic Requirements Checklist + its coverage audit (Batch 2), attached
    # post-generation by agents.test_scenario_agent whenever a checklist was
    # produced -- unconditional since 2026-08-14, when
    # QA_ATOMIC_CHECKLIST_ENABLED was deleted and the behaviour hardcoded ON.
    # A PrivateAttr so it is EXCLUDED from the JSON schema used as an LLM
    # response_model (it must never pollute generation) and from serialization.
    # tools.xlsx_generator and handlers read it via getattr.
    _checklist_artifacts: Optional[dict] = PrivateAttr(default=None)

    # {tc_id: note} for the XLSX Notes column — the Batch 3
    # standing-rules pack's mechanical [ASSUMED] /
    # [NEEDS-CLARIFICATION] label, attached post-renumber by
    # agents.test_scenario_agent. A PrivateAttr so it is
    # EXCLUDED from the JSON schema used as an LLM
    # response_model and from serialization.
    # tools.xlsx_generator reads it via getattr.
    _rule_pack_notes: Optional[dict] = PrivateAttr(default=None)

    # Rows for the "Assumed Requirements" sheet: the cases an entailment review
    # judged ungrounded, moved OFF the executable suite rather than deleted
    # (host_mode.grounding_review_enabled, hardcoded OFF 2026-08-13). A
    # PrivateAttr for the same reasons as
    # the two above -- it must not appear in the JSON schema handed to a model.
    _assumed_artifacts: Optional[dict] = PrivateAttr(default=None)

    # Rows for the "Generation Notes" sheet: what this run could NOT do, or did
    # short of what was asked -- an under-generated category, an absent
    # requirements checklist, a preflight that left no evidence it ran. A list
    # of (finding, detail) pairs, attached by handlers just before the
    # export.
    #
    # WHY IT EXISTS (2026-08-15, F5/F6/F7): each of these was already disclosed
    # in the tool reply, and each was measured surviving into a finalized suite
    # anyway -- because the reply is transient chat that a summarising host
    # model prunes, while the .xlsx is the artifact the tester keeps, attaches
    # to a ticket and reads a week later. A caveat that does not travel with the
    # deliverable is not a caveat. A PrivateAttr for the same reasons as the
    # attributes above: it must never appear in the JSON schema handed to a
    # model, and must not be serialized.
    _generation_notes: Optional[list] = PrivateAttr(default=None)

    # Rows for the "Requirements Traceability" sheet: one per acceptance
    # criterion, with the case COUNT and the linked tc_ids. Attached by
    # agents.test_scenario_agent._finalize_generation -- the one place holding
    # both the final renumbered cases and the ACs.
    #
    # WHY IT EXISTS (F06, 2026-08-19): the finalize reply claims "N/N acceptance
    # criteria traced", and until this sheet existed that claim could only be
    # checked against data/suites.db -- `requirement_id` was on every case and on
    # no exported column. A PrivateAttr for the same reasons as the attributes
    # above: it must never appear in the JSON schema handed to a model, and must
    # not be serialized.
    _rtm_artifacts: Optional[dict] = PrivateAttr(default=None)

    @field_validator("test_cases", mode="after")
    @classmethod
    def validate_unique_ids(cls, cases: list[TestCase]) -> list[TestCase]:
        ids = [tc.tc_id for tc in cases]
        if len(ids) != len(set(ids)):
            seen: set[str] = set()
            dupes = [x for x in ids if x in seen or seen.add(x)]  # type: ignore[func-returns-value]
            raise ValueError(f"Duplicate TC IDs found: {dupes}")
        return cases
