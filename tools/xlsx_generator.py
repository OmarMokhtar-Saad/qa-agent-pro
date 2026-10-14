"""Convert a TestSuite into a professional XLSX workbook using XlsxWriter."""

from __future__ import annotations

import logging
import math  # noqa: F401
import tempfile  # noqa: F401  # tests patch tools.<module>.tempfile.gettempdir
from typing import Any, NamedTuple  # noqa: F401

import xlsxwriter  # noqa: F401

from tools.bilingual import bidi_isolate, is_rtl_cell  # noqa: F401
from tools.cell_sanitizer import sanitize_cell  # noqa: F401
from tools.export_common import resolve_output_path  # noqa: F401
from tools.models import (
    TestSuite,  # noqa: F401
    display_requirement_id,  # noqa: F401
    format_test_data_lines,  # noqa: F401
)
from tools.secure_temp import (  # noqa: F401
    cleanup_temp_files as _sweep_temp_files,
)

# Re-exports (split into tools/xlsx_*.py); every name stays importable here.
from tools.xlsx_book import (  # noqa: F401
    _write_workbook,
    cleanup_temp_files,
    generate_test_case_xlsx,
)
from tools.xlsx_cases import (  # noqa: F401
    _rule_pack_notes,
    _sorted_cases,
    _tc_id_key,
    _write_case_row,
    _write_cases_sheet,
    _write_header,
    _write_leading,
    _write_trailing,
)
from tools.xlsx_cells import (  # noqa: F401
    _LINE_HEIGHT_PT,
    _MIN_ROW_HEIGHT,
    _notes_cell,
    _prepare,
    _requirement_cell,
    _risk_score_cell,
    _row_height_for,
)
from tools.xlsx_cols import (  # noqa: F401
    _CELL_ERRORS,
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
    _SHEET_ERRORS,
    _TOTAL_COLS,
    _UNTRACED_LABEL,
    _col_letter,
)
from tools.xlsx_extra import (  # noqa: F401
    _notes_sheet,
    _set_rtm_columns,
    _split_note,
    _write_generation_notes_sheet,
    _write_rtm_sheet,
)
from tools.xlsx_formats import (  # noqa: F401
    _apply_rtl,
    _build_case_formats,
    _case_cell_format,
    _case_header_format,
    _CaseFormats,
    _priority_fill_formats,
    _status_base_format,
)
from tools.xlsx_grid import (  # noqa: F401
    _checklist_sheet,
    _sheet_formats,
    _write_assumed_sheet,
    _write_checklist_sheets,
    _write_grid,
)
from tools.xlsx_row import (  # noqa: F401
    _case_texts,
    _CaseTexts,
    _data_text,
    _row_cells,
    _RowWriter,
    _step_texts,
)
from tools.xlsx_rules import (  # noqa: F401
    _add_priority_rules,
    _add_status_rules,
    _equal_rule,
    _priority_rule_formats,
    _status_dropdown,
    _status_rule_formats,
)
from tools.xlsx_summary import (  # noqa: F401
    _TYPES,
    _summary_formats,
    _summary_rows,
    _total_row,
    _write_priority_block,
    _write_summary_sheet,
    _write_type_block,
)

logger = logging.getLogger(__name__)
