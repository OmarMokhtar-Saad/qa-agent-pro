"""Column layout and error tuples shared by the XLSX workbook writers."""

from __future__ import annotations

import xlsxwriter

# What a cell renderer can raise on a malformed case: bad value types or a
# missing attribute (``OverflowError`` covers ``int(inf)``).
_CELL_ERRORS = (ValueError, TypeError, AttributeError, OverflowError)
# What a sheet writer can raise: the above, plus bad rows or xlsxwriter itself
# refusing the sheet (duplicate name, closed workbook, bad range).
_SHEET_ERRORS = (
    *_CELL_ERRORS,
    LookupError,
    ImportError,
    OSError,
    xlsxwriter.exceptions.XlsxWriterException,
)

# One row per test case — steps and expected results joined with newlines.
# Risk Score / Risk Label / Risk Rationale / Stable ID are intentionally NOT
# exported here — they're internal fields (risk_scorer sorting, dedup and the
# TestRail push all still read them off the TestCase model), just not shown as
# columns in the tester-facing file.
#
# `requirement_id` LEFT that list on 2026-08-19 (F06). It was a defensible
# "internal field" while it was only a sort/dedup key; it stopped being one when
# the finalize reply started claiming "N/N acceptance criteria traced", because
# the evidence for that claim then lived only in data/suites.db and the person
# holding the workbook could not check it. It is column M, and the Requirements
# Traceability sheet gives it the per-AC counts.
_COL_TCID = 0
_COL_MODULE = 1
_COL_TITLE = 2
_COL_PRIORITY = 3
_COL_TYPE = 4
_COL_PRECOND = 5
_COL_STEPS = 6  # "1. action\n2. action\n3. action"
_COL_TESTDATA = 7  # "Step 1: data\nStep 2: data" (only steps with data)
_COL_EXPECTED = 8  # "1. result\n2. result\n3. result"
_COL_STATUS = 9
_COL_NOTES = 10
# F6: APPENDED, never inserted. Every hardcoded column letter below (D for
# Priority, J for Status, A for TC ID) is derived from these indices via
# _col_letter(), so a future insert cannot leave a stale letter behind.
_COL_CATEGORY = 11
# F06 (2026-08-19): APPENDED for the same reason _COL_CATEGORY was, never
# inserted -- every hardcoded column letter is derived from these indices.
_COL_REQUIREMENT = 12
# v1.97.0 cursor-hardening (item 12): APPENDED, never inserted -- same
# reasoning as _COL_CATEGORY/_COL_REQUIREMENT above. Sortable/filterable risk
# score, replacing the "Risk <score>" text that used to be glued into Notes.
_COL_RISK_SCORE = 13
_TOTAL_COLS = 14


def _col_letter(index: int) -> str:
    """0-based column index -> spreadsheet letter (0 -> A, 25 -> Z, 26 -> AA)."""
    letters = ""
    n = int(index)
    if n < 0:
        # -1 % 26 == 25 would silently return a plausible "Z".
        raise ValueError(f"column index must be >= 0, got {index!r}")
    while True:
        letters = chr(ord("A") + (n % 26)) + letters
        n = n // 26 - 1
        if n < 0:
            return letters


# 2026-08-04: "Type" and "Category" read as duplicates to testers -- 25/97 rows
# of that day's run were literally equal and 24 more were the same value under a
# longer name. They are NOT duplicates: four generation categories (Positive /
# Happy Path, Edge Cases, State Transitions, UI/UX Validation) all collapse to
# the Functional TestType, so 48/97 rows carry category detail the type cannot
# express. The fix is naming, not data -- "Test Type" is the TestCase.type enum,
# "Coverage Category" is which of the 8 generation categories produced the case.
_HEADERS = [
    "TC ID",
    "Module",
    "Title",
    "Priority",
    "Test Type",
    "Preconditions",
    "Steps / Actions",
    "Test Data",
    "Expected Results",
    "Status",
    "Notes",
    "Coverage Category",
    "Requirement ID",
    "Risk Score",
]

# Index-parallel to _HEADERS (test_column_constants_stay_in_lockstep) and also
# the row-height autofit input. Column L went 22 -> 24: "Coverage Category" is 17
# chars and the widest value, "Negative / Error Flows", is 22.
_COL_WIDTHS = [10, 18, 30, 12, 14, 28, 45, 28, 45, 12, 20, 24, 16, 11]

_UNTRACED_LABEL = "(untraced)"
