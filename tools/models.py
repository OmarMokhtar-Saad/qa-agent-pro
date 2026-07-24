from __future__ import annotations

import hashlib
import uuid
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


def _compute_stable_id(title: str, steps: list["TestStep"]) -> str:
    """Deterministic content hash of a test case's title + ordered steps.

    Unlike the display-oriented ``tc_id`` (which is reassigned TC-001..N in final
    risk order on every generation), the stable id is derived purely from the
    case's semantic content, so the same test case keeps the same id across
    regenerations, exports and persisted runs (QW-13 / I-025). It is what
    downstream persistence (T-01), TMS push (T-10) and dedup key off.
    """
    payload = title.strip() + "␟".join(
        f"␞{s.action.strip()}␞{(s.test_data or '').strip()}␞{s.expected_result.strip()}"
        for s in steps
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"SID-{digest}"


class Priority(str, Enum):
    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class TestType(str, Enum):
    FUNCTIONAL = "Functional"
    REGRESSION = "Regression"
    SMOKE = "Smoke"
    INTEGRATION = "Integration"
    EXPLORATORY = "Exploratory"
    ACCESSIBILITY = "Accessibility"
    PERFORMANCE = "Performance"
    SECURITY = "Security"
    BOUNDARY = "Boundary"
    NEGATIVE = "Negative"


class AutomationStatus(str, Enum):
    AUTOMATED = "Automated"
    MANUAL = "Manual"
    TO_BE_AUTOMATED = "To Be Automated"
    CANNOT_BE_AUTOMATED = "Cannot Be Automated"
    NOT_APPLICABLE = "Not Applicable"


class TestStep(BaseModel):
    model_config = {"extra": "forbid"}

    step_number: int = Field(ge=1, description="Sequential step number starting at 1")
    action: str = Field(min_length=5, description="What the tester should do")
    test_data: Optional[str] = Field(
        None, description="Concrete test data, e.g. 'email: test@example.com'"
    )
    expected_result: str = Field(
        min_length=5, description="Observable, verifiable outcome"
    )

    @field_validator("action", "expected_result", mode="after")
    @classmethod
    def strip_and_check_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Field cannot be empty or whitespace only")
        return v


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

    @field_validator("test_cases", mode="after")
    @classmethod
    def validate_unique_ids(cls, cases: list[TestCase]) -> list[TestCase]:
        ids = [tc.tc_id for tc in cases]
        if len(ids) != len(set(ids)):
            seen: set[str] = set()
            dupes = [x for x in ids if x in seen or seen.add(x)]  # type: ignore[func-returns-value]
            raise ValueError(f"Duplicate TC IDs found: {dupes}")
        return cases
