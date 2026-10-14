"""Plain grid sheets: Requirements Checklist and Assumed Requirements."""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

import xlsxwriter

from tools.atomic_checklist import checklist_from_dicts, checklist_rows
from tools.cell_sanitizer import sanitize_cell
from tools.models import TestSuite
from tools.xlsx_cols import _SHEET_ERRORS

logger = logging.getLogger("tools.xlsx_generator")


class _GridFormats(NamedTuple):
    """Header and body formats of one plain grid sheet."""

    header: xlsxwriter.format.Format
    cell: xlsxwriter.format.Format


def _sheet_formats(workbook: xlsxwriter.Workbook, header_bg: str) -> _GridFormats:
    """Header and body formats shared by the plain grid sheets."""
    header_fmt = workbook.add_format(
        {
            "bold": True,
            "font_color": "#FFFFFF",
            "bg_color": header_bg,
            "border": 1,
            "valign": "vcenter",
            "text_wrap": True,
        }
    )
    cell_fmt = workbook.add_format({"border": 1, "valign": "top", "text_wrap": True})
    return _GridFormats(header_fmt, cell_fmt)


def write_grid(ws: Any, rows: list, fmts: _GridFormats) -> None:
    """Write ``rows`` from A1, row 0 in the header format, every cell sanitised."""
    header_fmt, cell_fmt = fmts
    for r, row in enumerate(rows):
        fmt = header_fmt if r == 0 else cell_fmt
        for c, value in enumerate(row):
            ws.write(r, c, sanitize_cell(str(value)), fmt)


# Append the 'Requirements Checklist' sheet when the suite carries
# ``_checklist_artifacts`` (unconditional since 2026-08-14 --
# QA_ATOMIC_CHECKLIST_ENABLED was deleted and the checklist hardcoded ON).
#
# A plain list of the parsed requirements.
#
# No-op when absent, so the workbook is byte-identical for a suite that
# carried no checklist.
# Never raises — a failure here must never break the core workbook. The
# checklist is a DURABLE artifact, which is why it gets its own sheet rather
# than a note in the summary.
def _write_checklist_sheets(workbook: xlsxwriter.Workbook, suite: TestSuite) -> None:
    artifacts = getattr(suite, "_checklist_artifacts", None)
    if not artifacts:
        return
    try:
        items = checklist_from_dicts(artifacts.get("items") or [])
        fmts = _sheet_formats(workbook, "#1F4E79")
        for name, rows in (("Requirements Checklist", checklist_rows(items)),):
            if not rows:
                continue
            _checklist_sheet(workbook, (name, rows), fmts)
    except _SHEET_ERRORS:
        logger.warning("checklist-sheet generation failed — skipping", exc_info=True)


def _checklist_sheet(
    workbook: xlsxwriter.Workbook, entry: tuple, fmts: _GridFormats
) -> None:
    name, rows = entry
    try:
        ws = workbook.add_worksheet(name)
        ws.set_column(0, 0, 14)
        ws.set_column(1, max(1, len(rows[0]) - 1), 42)
        write_grid(ws, rows, fmts)
    except _SHEET_ERRORS:
        logger.warning("Failed writing the %s sheet — skipping it", name, exc_info=True)


# Write the "Assumed Requirements" sheet, if an entailment review produced one.
#
# These cases assert behaviour the ticket never states. They are kept -- one may
# be a real requirement nobody wrote down -- but off the executable sheet, where
# they would file defects against a team that never agreed to the behaviour.
#
# No-op when absent, so the workbook is byte-identical on the flag-off path.
# Never raises: a failure here must never break the core workbook.
def _write_assumed_sheet(workbook: xlsxwriter.Workbook, suite: TestSuite) -> None:
    artifacts = getattr(suite, "_assumed_artifacts", None)
    if not artifacts:
        return
    try:
        rows = artifacts.get("rows") or []
        if not rows:
            return
        fmts = _sheet_formats(workbook, "#8B5E00")
        ws = workbook.add_worksheet("Assumed Requirements")
        ws.set_column(0, 1, 14)
        ws.set_column(2, max(2, len(rows[0]) - 1), 42)
        write_grid(ws, rows, fmts)
    except _SHEET_ERRORS:
        logger.warning(
            "Failed writing the Assumed Requirements sheet - skipping it",
            exc_info=True,
        )


# The 'AC Validation' / 'Test Plan' report sheets were DELETED 2026-08-30.
# TEST_PLAN_JOB went in dead-code deletion P2-H (2026-08-16) and was the only
# writer of ``TestSuite._report_artifacts``, so both sheets had been provably
# unreachable since -- no tester ever saw one in that window. P2-H recorded
# removing them as a PRODUCT decision rather than a deletion; that decision was
# taken on 2026-08-30, together with tools/test_plan_report.py and the private
# attribute itself.


def _write_grid(ws: Any, rows: list, fmts: _GridFormats) -> None:
    """Legacy name of :func:`write_grid` (WX removes it)."""
    return write_grid(ws, rows, fmts)
