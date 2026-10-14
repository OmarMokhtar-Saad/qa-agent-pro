"""Finding records for step_assertion (see tools/step_assertion.py)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StepAssertionFinding:
    """One step whose expected_result asserts nothing the action did not say."""

    tc_id: str
    category: str
    step_number: int
    kind: str  # "restates-action" | "generic-phrase"
    overlap: float
    expected: str


@dataclass(frozen=True)
class DataFinding:
    """One test_data entry whose value merely restates its field name.

    Disclosed, never refused: it is a readability defect, not a correctness
    one.
    """

    tc_id: str
    field: str
    example_value: str
    kind: str  # "echoes-field-name"
