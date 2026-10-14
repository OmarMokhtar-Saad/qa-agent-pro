"""One 'Test Cases' row: the row writer and the derived cell texts."""

from __future__ import annotations

from typing import Any, NamedTuple

from tools.bilingual import is_rtl_cell
from tools.models import format_test_data_lines
from tools.xlsx_cells import _notes_cell, _prepare, _requirement_cell
from tools.xlsx_cols import (
    _COL_CATEGORY,
    _COL_EXPECTED,
    _COL_MODULE,
    _COL_NOTES,
    _COL_PRECOND,
    _COL_PRIORITY,
    _COL_REQUIREMENT,
    _COL_STEPS,
    _COL_TCID,
    _COL_TESTDATA,
    _COL_TITLE,
    _COL_TYPE,
    _COL_WIDTHS,
)


class _RowFmts(NamedTuple):
    """The plain and right-to-left formats of one row."""

    plain: object
    rtl: object


class _RowWriter:
    """Writes the cells of one 'Test Cases' row with its alternating format."""

    def __init__(self, ws: Any, row_idx: int, fmts: _RowFmts):
        fmt, rtl_fmt = fmts
        self._ws = ws
        self._row = row_idx
        self._fmt = fmt
        self._rtl_fmt = rtl_fmt

    def text(self, col: int, text: str) -> None:
        """One text cell: sanitised, bidi-isolated, and RTL-formatted
        when the content is Arabic-majority. Applied to EVERY text column
        (Module, Title, Preconditions, Steps, Test Data, Expected
        Results, Notes) -- an Arabic message can legitimately land in any
        of them, and a cell that is right-to-left in one column and
        left-to-right in the next reads as a rendering bug."""
        value = _prepare(text)
        fmt = self._rtl_fmt if is_rtl_cell(text or "") else self._fmt
        self._ws.write(self._row, col, value, fmt)

    def prepared(self, col: int, text: str) -> None:
        """A cell whose text already went through _prepare (multi-line cells)."""
        fmt = self._rtl_fmt if is_rtl_cell(text) else self._fmt
        self._ws.write(self._row, col, text, fmt)

    def risk_score(self, col: int, score: int | None) -> None:
        # v1.97.0 cursor-hardening (item 12): sortable/filterable risk score,
        # its own column rather than text glued into Notes (see _notes_cell).
        if score is None:
            self._ws.write_blank(self._row, col, None, self._fmt)
        else:
            self._ws.write_number(self._row, col, score, self._fmt)


class _CaseTexts(NamedTuple):
    """The computed multi-line / derived cell texts of one test case."""

    steps: str
    expected: str
    test_data: str
    notes: str
    requirement: str


def _step_texts(tc: Any) -> tuple[str, str]:
    # Combine steps into a single multi-line string: "1. action\n2. action"
    # Wrapped in sanitize_cell() -- this text originates from LLM-generated or
    # Jira-derived content and must not be interpreted as a spreadsheet formula.
    steps_text = _prepare("\n".join(f"{s.step_number}. {s.action}" for s in tc.steps))

    # Combine expected results: "1. result\n2. result"
    expected_text = _prepare(
        "\n".join(f"{s.step_number}. {s.expected_result}" for s in tc.steps)
    )
    return steps_text, expected_text


def _data_text(tc: Any) -> str:
    # Combine test data only for steps that have it: "Step N: data"
    data_lines = [
        f"Step {s.step_number}: {s.test_data}" for s in tc.steps if s.test_data
    ]
    # Case-level data-provisioning plan. Only present when the case declared
    # test_data; appended after the per-step lines so a case with none renders
    # byte-identically to before.
    data_lines.extend(format_test_data_lines(tc.test_data))
    return _prepare("\n".join(data_lines))


def _case_texts(tc: Any, rule_pack_note: str) -> _CaseTexts:
    steps_text, expected_text = _step_texts(tc)
    return _CaseTexts(
        steps=steps_text,
        expected=expected_text,
        test_data=_data_text(tc),
        notes=_notes_cell(tc, rule_pack_note),
        requirement=_requirement_cell(tc),
    )


def _row_cells(tc: Any, texts: _CaseTexts) -> list:
    """(text, column width) per cell, for fitting the row height to the
    tallest cell (wrapped text included), not just the step count -- long
    titles/preconditions/data/expected results no longer clip. Column widths
    stay fixed (_COL_WIDTHS)."""
    return [
        (texts.notes, _COL_WIDTHS[_COL_NOTES]),
        (getattr(tc, "category", None) or "", _COL_WIDTHS[_COL_CATEGORY]),
        (texts.requirement, _COL_WIDTHS[_COL_REQUIREMENT]),
        (tc.tc_id, _COL_WIDTHS[_COL_TCID]),
        (tc.module, _COL_WIDTHS[_COL_MODULE]),
        (tc.title, _COL_WIDTHS[_COL_TITLE]),
        (tc.priority.value, _COL_WIDTHS[_COL_PRIORITY]),
        (tc.type.value, _COL_WIDTHS[_COL_TYPE]),
        (tc.preconditions or "", _COL_WIDTHS[_COL_PRECOND]),
        (texts.steps, _COL_WIDTHS[_COL_STEPS]),
        (texts.test_data, _COL_WIDTHS[_COL_TESTDATA]),
        (texts.expected, _COL_WIDTHS[_COL_EXPECTED]),
    ]
