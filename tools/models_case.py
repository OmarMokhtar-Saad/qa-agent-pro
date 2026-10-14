from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from tools.models_core import (
    AutomationStatus,
    Priority,
    TestDataItem,
    TestStep,
    TestType,
    _compute_stable_id,
)


class TestCase(BaseModel):
    model_config = {"extra": "forbid"}

    tc_id: str = Field(
        pattern=r"^TC-\d{3,6}$",
        description="Unique identifier, format TC-001 through TC-999999, sequential from TC-001",
    )
    module: str = Field(
        min_length=2, max_length=100, description="Feature or module name"
    )
    title: str = Field(
        min_length=10, max_length=250, description="Concise test case title"
    )
    priority: Priority = Field(
        description="Exactly one of: Critical, High, Medium, Low"
    )
    type: TestType = Field(
        description="Exactly one of: Functional, Regression, Smoke, Integration, "
        "Exploratory, Accessibility, Performance, Security, Boundary, Negative"
    )
    preconditions: Optional[str] = Field(
        None, description="System state required before test execution"
    )
    steps: list[TestStep] = Field(
        min_length=1, description="Ordered steps numbered sequentially from 1"
    )
    postconditions: Optional[str] = Field(
        None, description="Expected system state after test execution"
    )
    automation_status: AutomationStatus = Field(
        default=AutomationStatus.MANUAL,
        description="Exactly one of: Automated, Manual, To Be Automated, Cannot Be Automated, Not Applicable",
    )
    requirement_id: Optional[str] = Field(
        None, description="Linked Jira ticket or requirement ID"
    )
    risk_score: int = Field(
        default=0,
        description="Heuristic risk score (higher = riskier); 0 = not yet scored",
    )
    risk_label: str = Field(
        default="",
        description="Risk tier: CRITICAL / HIGH / MEDIUM / LOW (empty = not yet scored)",
    )
    risk_rationale: str = Field(
        default="", description="Human-readable explanation of the risk score"
    )
    stable_id: str = Field(
        default="",
        description="Auto-generated content hash — LEAVE EMPTY. The system derives "
        "it from the title and steps so the case keeps a stable identity across "
        "regenerations and exports, independent of the display-order tc_id.",
    )
    category: Optional[str] = Field(
        default=None,
        max_length=60,
        description="Which of the 8 generation categories produced this case "
        "(Positive / Happy Path, Negative / Error Flows, Boundary Values, Edge "
        "Cases, State Transitions, Security, UI/UX Validation, Integration). "
        "Bounded free text: normalised at the untrusted boundary, empty when it "
        "could not be resolved -- never guessed.",
    )
    category_source: Optional[Literal["server", "host"]] = Field(
        default=None,
        description='Where `category` came from. "server": derived by the server (the fan-out category, or the category_name argument of a qa_submit_category call). "host": self-reported by the tester\'s chat model on a single merged submission, where the server has no grouping of its own. Set in code on every path -- a model-supplied value is always overwritten -- so a re-export can still tell the two apart.',
    )
    test_data: list[TestDataItem] = Field(
        default_factory=list,
        description="Optional per-case data-provisioning plan: for each data field "
        "the test needs, how to source its value (unique per run / seed account / "
        "chained from an earlier case / static) with a SAFE fake example. Empty "
        "for a case that manipulates no data.",
    )

    @field_validator("tc_id", mode="before")
    @classmethod
    def _normalize_tc_id(cls, v):
        """Rewrite common tc_id variants (`tc-1`, `TC_001`, `TC- 001`, `TC1`)
        to the canonical `TC-NNN` form before the `pattern=` constraint above
        ever sees them. Anything that still does not look like a test case id
        is left untouched so the Field pattern's own error names the
        offending value -- this validator never widens what counts as valid,
        only normalizes what already does.

        Air onboarding fix P1-6: the specific malformed id from that run was
        not in synced evidence, so this targets the documented common-variant
        class rather than one literal string.
        """
        if not isinstance(v, str):
            return v
        raw = v.strip()
        match = re.match(r"(?i)^tc[\s_-]*(\d{1,6})$", raw)
        if not match:
            return v
        digits = match.group(1)
        return f"TC-{digits.zfill(3)}"

    # Mirror of TestStep.strip_and_check_not_empty, for the same reason.
    #
    # 2026-08-30 audit F7: pydantic measures ``min_length=10`` against the RAW
    # string, so a title of twelve spaces was accepted -- and then had a
    # stable_id derived from it, was risk-scored, renumbered and written into
    # the tester's workbook as a blank row heading. Stripping also keeps the
    # content stable_id stable: a padded and an unpadded copy of one case must
    # not read as two cases.
    #
    # Deliberately WHITESPACE-ONLY, not a re-check of ``min_length`` against
    # the stripped value (review round 2, M1). Re-checking would NARROW the
    # constraint, and ``tools/suite_store._load_suite`` retries a failed
    # validation only after dropping unknown keys -- its own comment says
    # "additive drift only; a narrowed constraint still fails below". A suite
    # stored with a padded short title would then come back with that case
    # silently missing on the next ``qa_export_suite`` / ``qa_push_suite``.
    # Rejecting a blank title costs nothing on the read path, because no case
    # with a blank title was ever worth reloading.
    @field_validator("title", mode="after")
    @classmethod
    def strip_and_check_title(cls, v: str) -> str:
        """Mirror of TestStep.strip_and_check_not_empty, for the same reason."""
        v = v.strip()
        if not v:
            raise ValueError("Field cannot be empty or whitespace only")
        return v

    @field_validator("steps", mode="after")
    @classmethod
    def validate_step_numbering(cls, steps: list[TestStep]) -> list[TestStep]:
        for i, step in enumerate(steps, start=1):
            if step.step_number != i:
                raise ValueError(
                    f"Step numbering must be sequential: expected {i}, got {step.step_number}"
                )
        return steps

    @model_validator(mode="after")
    def _assign_stable_id(self) -> "TestCase":
        # Always (re)derive the stable id from content so it is deterministic and
        # cannot be spoofed by an LLM/attacker supplying its own value. Recomputing
        # on a round-tripped case yields the same id (content is unchanged).
        self.stable_id = _compute_stable_id(self.title, self.steps)
        return self
