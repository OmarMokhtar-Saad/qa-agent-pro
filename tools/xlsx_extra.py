"""Requirements Traceability and Generation Notes sheets."""

from __future__ import annotations

import logging

import xlsxwriter

from tools import xlsx_grid
from tools.cell_sanitizer import sanitize_cell
from tools.models import TestSuite
from tools.xlsx_cols import _SHEET_ERRORS
from tools.xlsx_grid import _sheet_formats

logger = logging.getLogger("tools.xlsx_generator")


# Append the 'Requirements Traceability' sheet when the suite carries
# ``_rtm_artifacts`` (attached in agents.test_scenario_agent._finalize_generation).
#
# F06 (2026-08-19): the finalize reply claims "N/N acceptance criteria traced";
# until this sheet existed that claim was checkable only against
# data/suites.db. One row per criterion WITH the case count, so an
# over-weighted criterion -- 25 of 96 cases on AC-001 against 3 on AC-002 in
# the 2026-08-16 run -- is legible instead of hidden behind a total.
#
# Written AHEAD of 'Requirements Checklist' deliberately: that sheet links
# `CL-*` ids, the model-derived atomic checklist, which is a different
# denominator with a different provenance. This one is about the `AC-*` ids the
# headline claim is actually made against.
#
# NO-OP WHEN THE SUITE CARRIES NO ARTIFACTS, which includes every re-export
# through qa_export_suite: suite_store.load_suite rebuilds a TestSuite from the
# `cases` table alone, so no private attribute survives and the checklist /
# report / assumed / generation-notes sheets are all absent there too. The
# Requirement ID COLUMN is unaffected (it is read off the case). Persisting the
# artifacts is its own change, scoped with F12, and is deliberately NOT done by
# re-parsing suites.feature_text -- that would rebuild a DIFFERENT AC list from
# the one the suite was judged against. Never raises: a failure here must never
# break the core workbook.
def _write_rtm_sheet(workbook: xlsxwriter.Workbook, suite: TestSuite) -> None:
    try:
        artifacts = getattr(suite, "_rtm_artifacts", None)
        rows = (artifacts or {}).get("rows") or []
        if not rows:
            return
        # rtm_rows' output is rendered as-is.
        fmts = _sheet_formats(workbook, "#1F4E79")
        ws = workbook.add_worksheet("Requirements Traceability")
        _set_rtm_columns(ws)
        xlsx_grid._write_grid(ws, rows, fmts)
    except _SHEET_ERRORS:
        logger.warning(
            "Failed writing the Requirements Traceability sheet - skipping it",
            exc_info=True,
        )


def _set_rtm_columns(ws: xlsxwriter.worksheet.Worksheet) -> None:
    ws.set_column(0, 0, 14)
    ws.set_column(1, 1, 72)
    ws.set_column(2, 2, 8)
    ws.set_column(3, 3, 42)
    ws.set_column(4, 4, 14)


# Write the "Generation Notes" sheet: what this run could not do.
#
# 2026-08-15 (F5/F6/F7). Under-generated categories, an absent requirements
# checklist and a preflight that left no evidence it ran were all disclosed in
# the tool reply and all still reached finalized, exported suites -- because
# the reply is transient chat a summarising host model prunes, while this
# workbook is what the tester keeps and attaches to a ticket. The caveat has
# to travel with the deliverable.
#
# Deliberately the LAST sheet: it must not displace the test cases as the
# thing that opens first, and its absence is the normal, healthy case.
#
# No-op when the suite carries no notes, so a clean run's workbook is
# byte-identical. Never raises -- a failure here must never break the core
# workbook.
def _write_generation_notes_sheet(
    workbook: xlsxwriter.Workbook, suite: TestSuite
) -> None:
    rows = getattr(suite, "_generation_notes", None)
    if not rows:
        return
    try:
        fmts = _sheet_formats(workbook, "#9C2C2C")
        _notes_sheet(workbook, rows, fmts)
    except _SHEET_ERRORS:
        logger.warning(
            "Failed writing the Generation Notes sheet - skipping it",
            exc_info=True,
        )


def _notes_sheet(workbook: xlsxwriter.Workbook, rows: list, fmts: tuple) -> None:
    header_fmt, cell_fmt = fmts
    ws = workbook.add_worksheet("Generation Notes")
    ws.set_column(0, 0, 30)
    ws.set_column(1, 1, 96)
    ws.write(0, 0, "Finding", header_fmt)
    ws.write(0, 1, "Detail", header_fmt)
    for r, row in enumerate(rows, start=1):
        finding, detail = _split_note(row)
        ws.write(r, 0, sanitize_cell(str(finding)), cell_fmt)
        ws.write(r, 1, sanitize_cell(str(detail)), cell_fmt)


def _split_note(row: object) -> tuple:
    try:
        finding, detail = row[0], row[1]  # type: ignore[index]
    except (TypeError, IndexError, KeyError):
        finding, detail = "Note", row
    return finding, detail
