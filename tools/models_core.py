from __future__ import annotations

import hashlib
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


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


class TestDataItem(BaseModel):
    """One field's data-provisioning plan for a test case.

    Declares WHAT data a case needs and HOW a manual tester should source it, so
    testers stop guessing which values must be unique per run, come from a seeded
    account, or chain from an earlier case. ``example_value`` MUST be an obviously
    fake placeholder — never real-looking PII.

    An unknown ``strategy`` deliberately raises ValidationError (mirroring Priority /
    TestType) so the per-category retry regenerates it — there is no lenient-coercion
    precedent in this module.
    """

    model_config = {"extra": "forbid"}

    field: str = Field(
        min_length=1, max_length=80, description="Data field name, e.g. 'username'"
    )
    strategy: Literal["unique_per_run", "seed_account", "chained", "static"] = Field(
        description="How the value is sourced: unique_per_run / seed_account / "
        "chained / static"
    )
    example_value: str = Field(
        default="",
        max_length=200,
        description="A SAFE, obviously-fake example value — never real-looking PII",
    )
    chained_from: Optional[str] = Field(
        default=None,
        description="tc_id of the earlier case that produces this value "
        "(only when strategy == 'chained')",
    )
    notes: str = Field(
        default="",
        max_length=200,
        description="Short hint on how to obtain/rotate the value",
    )


def format_test_data_lines(items: list["TestDataItem"]) -> list[str]:
    """Render a case's test_data plan into compact one-per-field display lines.

    Pure and never-raises; returns [] for an empty/None plan so callers that only
    render when a case HAS test_data stay byte-identical to the pre-feature output.
    """
    lines: list[str] = []
    try:
        for it in items or []:
            chain = (
                f" (from {it.chained_from})"
                if it.strategy == "chained" and it.chained_from
                else ""
            )
            example = f": {it.example_value}" if it.example_value else ""
            note = f" — {it.notes}" if it.notes else ""
            lines.append(f"{it.field} [{it.strategy}]{chain}{example}{note}")
    except (AttributeError, TypeError):
        return lines
    return lines
