"""Workbook assembly: generate the XLSX file and sweep old temp files."""

from __future__ import annotations

import logging

import xlsxwriter

from tools.export_common import resolve_output_path
from tools.models import TestSuite
from tools.secure_temp import (
    Sweep,
)
from tools.secure_temp import (
    cleanup_temp_files as _sweep_temp_files,
)
from tools.xlsx_cases import _write_cases_sheet
from tools.xlsx_extra import _write_generation_notes_sheet, _write_rtm_sheet
from tools.xlsx_grid import _write_assumed_sheet, _write_checklist_sheets
from tools.xlsx_summary import _write_summary_sheet

logger = logging.getLogger("tools.xlsx_generator")


def generate_test_case_xlsx(suite: TestSuite, output_path: str | None = None) -> str:
    """Write suite to an XLSX file and return the file path."""
    output_path = resolve_output_path(output_path, ".xlsx")

    workbook = xlsxwriter.Workbook(output_path, {"strings_to_formulas": False})
    try:
        _write_workbook(workbook, suite)
        _write_rtm_sheet(workbook, suite)
        _write_checklist_sheets(workbook, suite)
        _write_assumed_sheet(workbook, suite)
        _write_generation_notes_sheet(workbook, suite)
    finally:
        workbook.close()

    logger.info("XLSX written: %s (%d test cases)", output_path, len(suite.test_cases))
    return output_path


def cleanup_temp_files(max_age_seconds: int = 3600) -> int:
    """Delete qa_test_cases_*.xlsx temp files older than max_age_seconds. Returns count deleted."""
    return _sweep_temp_files(
        Sweep(
            max_age_seconds=max_age_seconds,
            pattern="qa_test_cases_*.xlsx",
            label="XLSX",
            sweep_tmp_root=True,
            require_subdir=False,
            log=logger,
        )
    )


def _write_workbook(workbook: xlsxwriter.Workbook, suite: TestSuite) -> None:
    last_data_row = _write_cases_sheet(workbook, suite)
    _write_summary_sheet(workbook, suite, last_data_row)
