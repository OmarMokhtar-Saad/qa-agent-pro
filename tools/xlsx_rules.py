"""Conditional-format rules and the dropdown of the 'Test Cases' sheet."""

from __future__ import annotations

from typing import Any

import xlsxwriter

from tools.xlsx_cols import _COL_PRIORITY, _COL_STATUS, _col_letter


def _priority_rule_formats(workbook: xlsxwriter.Workbook) -> list:
    """(priority value, conditional-format Format) pairs, in rule order."""
    pairs = []
    for value, bg, font, bold in (
        ("Critical", "#FF4444", "#FFFFFF", True),
        ("High", "#FFC7CE", "#9C0006", False),
        ("Medium", "#FFEB9C", "#9C6500", False),
        ("Low", "#C6EFCE", "#006100", False),
    ):
        props = {"bg_color": bg, "font_color": font, "border": 1, "valign": "top"}
        if bold:
            props["bold"] = True
        pairs.append((value, workbook.add_format(props)))
    return pairs


def _equal_rule(value: str, fmt: object) -> dict:
    return {
        "type": "cell",
        "criteria": "equal to",
        "value": f'"{value}"',
        "format": fmt,
    }


def _add_priority_rules(
    workbook: xlsxwriter.Workbook, ws: Any, last_data_row: int
) -> None:
    """Conditional format on the Priority column, letter DERIVED from the index."""
    _pri = _col_letter(_COL_PRIORITY)
    pri_range = f"{_pri}2:{_pri}{last_data_row}"
    for value, fmt in _priority_rule_formats(workbook):
        ws.conditional_format(pri_range, _equal_rule(value, fmt))


def _status_rule_formats(workbook: xlsxwriter.Workbook) -> list:
    """(status value, conditional-format Format) pairs, in rule order."""
    # (no align — Excel ignores it in cond. formats)
    base = {"border": 1, "align": "center", "valign": "vcenter"}
    pairs = []
    for value, bg, font, bold in (
        ("Pass", "#C6EFCE", "#006100", True),
        ("Fail", "#FFC7CE", "#9C0006", True),
        ("Blocked", "#FFEB9C", "#9C6500", True),
        ("Not Run", "#D9D9D9", "#595959", False),
        ("In Progress", "#BDD7EE", "#1F4E79", True),
        ("Skipped", "#E2EFDA", "#375623", False),
    ):
        props = {**base, "bg_color": bg, "font_color": font}
        if bold:
            props["bold"] = True
        pairs.append((value, workbook.add_format(props)))
    return pairs


def _status_dropdown(ws: Any, last_data_row: int) -> None:
    # Status dropdown with tooltip indicator
    ws.data_validation(
        f"{_col_letter(_COL_STATUS)}2:{_col_letter(_COL_STATUS)}{last_data_row}",
        {
            "validate": "list",
            "source": ["Not Run", "Pass", "Fail", "Blocked", "In Progress", "Skipped"],
            "input_title": "Select Status",
            "input_message": "Choose a test status from the list",
        },
    )


def _add_status_rules(
    workbook: xlsxwriter.Workbook, ws: Any, last_data_row: int
) -> None:
    """Status column: colour-coded conditional formats plus the dropdown."""
    _stat = _col_letter(_COL_STATUS)
    status_col_range = f"{_stat}2:{_stat}{last_data_row}"
    for value, fmt in _status_rule_formats(workbook):
        ws.conditional_format(status_col_range, _equal_rule(value, fmt))

    _status_dropdown(ws, last_data_row)
