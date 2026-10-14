"""The 'Test Cases' sheet: header, TC-ID ordering and one row per case."""

from __future__ import annotations

from typing import Any, NamedTuple

import xlsxwriter

from tools.models import TestSuite
from tools.xlsx_cells import _risk_score_cell, _row_height_for
from tools.xlsx_cols import (
    _COL_CATEGORY,
    _COL_EXPECTED,
    _COL_MODULE,
    _COL_NOTES,
    _COL_PRECOND,
    _COL_PRIORITY,
    _COL_REQUIREMENT,
    _COL_RISK_SCORE,
    _COL_STATUS,
    _COL_STEPS,
    _COL_TCID,
    _COL_TESTDATA,
    _COL_TITLE,
    _COL_TYPE,
    _COL_WIDTHS,
    _HEADERS,
    _TOTAL_COLS,
)
from tools.xlsx_formats import _build_case_formats, _CaseFormats
from tools.xlsx_row import _case_texts, _CaseTexts, _row_cells, _RowFmts, _RowWriter
from tools.xlsx_rules import _add_priority_rules, _add_status_rules


def _tc_id_key(tc: Any) -> int:
    """Sort key: the digits of the TC-ID (0 when it has none)."""
    digits = "".join(ch for ch in (getattr(tc, "tc_id", "") or "") if ch.isdigit())
    return int(digits) if digits else 0


def _write_header(ws: Any, fmts: _CaseFormats) -> None:
    for i, w in enumerate(_COL_WIDTHS):
        ws.set_column(i, i, w)

    ws.freeze_panes(1, 0)
    ws.autofilter(0, 0, 0, _TOTAL_COLS - 1)
    ws.write_row(0, 0, _HEADERS, fmts.header)
    ws.set_row(0, 22)


# Present rows in TC-ID order. The agent already assigns TC-IDs in final
# risk order (highest-risk = TC-001), so sorting by TC-ID keeps the sheet's
# row order identical to the IDs — never re-sort by priority/type here, or the
# visible IDs would no longer be sequential (that was a reported bug).
def _sorted_cases(suite: TestSuite) -> list:
    return sorted(suite.test_cases, key=_tc_id_key)


# Notes for the Notes column: the Batch 3 standing-rules pack attaches
# a mechanical assumption / clarification label per tc_id. Absent =>
# every Notes cell stays empty, exactly as before.
def _rule_pack_notes(suite: TestSuite) -> dict:
    return getattr(suite, "_rule_pack_notes", None) or {}


def _write_cases_sheet(workbook: xlsxwriter.Workbook, suite: TestSuite) -> int:
    """Write the 'Test Cases' sheet; returns the last data row (1-based)."""
    fmts = _build_case_formats(workbook)
    ws = workbook.add_worksheet("Test Cases")
    _write_header(ws, fmts)
    notes = _rule_pack_notes(suite)

    for row_idx, tc in enumerate(_sorted_cases(suite), start=1):
        row = _CaseRow(ws, row_idx, tc, fmts)
        _write_case_row(row, notes.get(tc.tc_id, ""))

    last_data_row = len(suite.test_cases) + 1
    _add_priority_rules(workbook, ws, last_data_row)
    _add_status_rules(workbook, ws, last_data_row)
    return last_data_row


class _CaseRow(NamedTuple):
    """The sheet, row index, case and formats shared by one row's writers."""

    ws: Any
    row_idx: int
    tc: Any
    fmts: _CaseFormats


def _write_leading(row: _CaseRow) -> None:
    ws, row_idx, tc, fmts = row
    odd = row_idx % 2 == 1
    fmt = fmts.odd if odd else fmts.even
    cell = _RowWriter(
        ws, row_idx, _RowFmts(fmt, fmts.odd_rtl if odd else fmts.even_rtl)
    )
    ws.write(row_idx, _COL_TCID, tc.tc_id, fmt)
    cell.text(_COL_MODULE, tc.module)
    cell.text(_COL_TITLE, tc.title)
    ws.write(
        row_idx,
        _COL_PRIORITY,
        tc.priority.value,
        fmts.priority_fill.get(tc.priority.value, fmt),
    )
    ws.write(row_idx, _COL_TYPE, tc.type.value, fmt)
    cell.text(_COL_PRECOND, tc.preconditions or "")


# F6: which of the 8 generation categories produced this case. Empty when
# it could not be resolved -- never guessed. A value self-reported by the
# host model is normalised before it reaches here.
# F06: the acceptance criterion this case verifies -- the evidence for
# the reply's "N/N traced" claim, in the file the tester keeps.
def _write_trailing(row: _CaseRow, texts: _CaseTexts) -> None:
    ws, row_idx, tc, fmts = row
    odd = row_idx % 2 == 1
    fmt = fmts.odd if odd else fmts.even
    status_fmt = fmts.status_odd if odd else fmts.status_even
    cell = _RowWriter(
        ws, row_idx, _RowFmts(fmt, fmts.odd_rtl if odd else fmts.even_rtl)
    )
    cell.prepared(_COL_STEPS, texts.steps)
    cell.prepared(_COL_TESTDATA, texts.test_data)
    cell.prepared(_COL_EXPECTED, texts.expected)
    ws.write(row_idx, _COL_STATUS, "Not Run", status_fmt)
    cell.text(_COL_NOTES, texts.notes)
    cell.text(_COL_CATEGORY, getattr(tc, "category", None) or "")
    cell.text(_COL_REQUIREMENT, texts.requirement)
    cell.risk_score(_COL_RISK_SCORE, _risk_score_cell(tc))


def _write_case_row(row: _CaseRow, rule_pack_note: str) -> None:
    """Write one test case as its row and size the row to its content."""
    texts = _case_texts(row.tc, rule_pack_note)
    _write_leading(row)
    _write_trailing(row, texts)
    row.ws.set_row(row.row_idx, _row_height_for(_row_cells(row.tc, texts)))
