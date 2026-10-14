"""The 'Summary' sheet: COUNTIF formulas over the 'Test Cases' columns."""

from __future__ import annotations

from typing import Any, NamedTuple

import xlsxwriter

from tools.models import TestSuite
from tools.xlsx_cols import (
    _COL_PRIORITY,
    _COL_STATUS,
    _COL_TCID,
    _COL_TYPE,
    _col_letter,
)


class _SummaryCtx(NamedTuple):
    """What the Priority and Type blocks need from the summary sheet."""

    summary_ws: Any
    suite: TestSuite
    last_data_row: int
    label_fmt: xlsxwriter.format.Format
    value_fmt: xlsxwriter.format.Format


_TYPES = (
    "Functional",
    "Negative",
    "Boundary",
    "Regression",
    "Smoke",
    "Integration",
    "Security",
    "Performance",
    "Accessibility",
    "Exploratory",
)


def _summary_formats(workbook: xlsxwriter.Workbook) -> tuple:
    title_fmt = workbook.add_format(
        {"bold": True, "font_size": 16, "font_color": "#1F4E79"}
    )
    label_fmt = workbook.add_format(
        {"bold": True, "bg_color": "#D6E4F0", "border": 1, "valign": "vcenter"}
    )
    value_fmt = workbook.add_format({"border": 1, "align": "center"})
    pct_fmt = workbook.add_format(
        {"border": 1, "align": "center", "num_format": "0.0%"}
    )
    return title_fmt, label_fmt, value_fmt, pct_fmt


def _total_row(total: int, last_data_row: int, value_fmt: object) -> tuple:
    return (
        "Total Test Cases",
        f"=COUNTA('Test Cases'!{_col_letter(_COL_TCID)}2:"
        f"{_col_letter(_COL_TCID)}{last_data_row})",
        value_fmt,
        total,
    )


def _summary_rows(total: int, last_data_row: int, fmts: tuple) -> list:
    value_fmt, pct_fmt = fmts
    _s = _col_letter(_COL_STATUS)
    status_range = f"'Test Cases'!{_s}2:{_s}{last_data_row}"
    return [
        _total_row(total, last_data_row, value_fmt),
        ("Pass", f'=COUNTIF({status_range},"Pass")', value_fmt, 0),
        ("Fail", f'=COUNTIF({status_range},"Fail")', value_fmt, 0),
        ("Blocked", f'=COUNTIF({status_range},"Blocked")', value_fmt, 0),
        ("Not Run", f'=COUNTIF({status_range},"Not Run")', value_fmt, total),
        ("In Progress", f'=COUNTIF({status_range},"In Progress")', value_fmt, 0),
        ("Skipped", f'=COUNTIF({status_range},"Skipped")', value_fmt, 0),
        ("Pass Rate", "=IFERROR(B4/B3,0)", pct_fmt, 0.0),
    ]


def _write_summary_sheet(
    workbook: xlsxwriter.Workbook, suite: TestSuite, last_data_row: int
) -> None:
    """The 'Summary' sheet: COUNTIF formulas over the 'Test Cases' columns."""
    summary_ws = workbook.add_worksheet("Summary")
    summary_ws.set_column("A:A", 25)
    summary_ws.set_column("B:B", 15)
    title_fmt, label_fmt, value_fmt, pct_fmt = _summary_formats(workbook)
    summary_ws.write("A1", "Test Execution Summary", title_fmt)
    summary_ws.set_row(0, 30)

    total = len(suite.test_cases)
    summary_rows = _summary_rows(total, last_data_row, (value_fmt, pct_fmt))
    for i, (label, formula, vfmt, cached) in enumerate(summary_rows, start=2):
        summary_ws.write(i, 0, label, label_fmt)
        summary_ws.write_formula(i, 1, formula, vfmt, cached)

    ctx = _SummaryCtx(summary_ws, suite, last_data_row, label_fmt, value_fmt)
    _write_priority_block(ctx)
    _write_type_block(ctx)


# 2026-07-30 run review, item 5: the Priority and Type blocks were HARDCODED
# literals while the Status block above was COUNTIF, so the moment a tester
# edited, added or deleted a row the SAME sheet contradicted itself. Same
# shape as summary_rows: a real formula plus the computed count as the CACHED
# value, so a viewer that ignores formulas still shows the right number.
# Column letters are DERIVED from the _COL_* indices, never hardcoded.
def _write_priority_block(ctx: _SummaryCtx) -> None:
    summary_ws, suite, last_data_row, label_fmt, value_fmt = ctx
    summary_ws.write("A11", "Priority", label_fmt)
    summary_ws.write("B11", "Count", label_fmt)
    _pri_col = _col_letter(_COL_PRIORITY)
    priority_range = f"'Test Cases'!{_pri_col}2:{_pri_col}{last_data_row}"
    for j, pri in enumerate(["Critical", "High", "Medium", "Low"], start=12):
        count = sum(1 for tc in suite.test_cases if tc.priority.value == pri)
        summary_ws.write(j, 0, pri, label_fmt)
        summary_ws.write_formula(
            j, 1, f'=COUNTIF({priority_range},"{pri}")', value_fmt, count
        )


def _write_type_block(ctx: _SummaryCtx) -> None:
    summary_ws, suite, last_data_row, label_fmt, value_fmt = ctx
    _type_col = _col_letter(_COL_TYPE)
    type_range = f"'Test Cases'!{_type_col}2:{_type_col}{last_data_row}"
    summary_ws.write("A17", "Test Type", label_fmt)
    summary_ws.write("B17", "Count", label_fmt)
    for j, ttype in enumerate(_TYPES, start=18):
        count = sum(1 for tc in suite.test_cases if tc.type.value == ttype)
        summary_ws.write(j, 0, ttype, label_fmt)
        summary_ws.write_formula(
            j, 1, f'=COUNTIF({type_range},"{ttype}")', value_fmt, count
        )
