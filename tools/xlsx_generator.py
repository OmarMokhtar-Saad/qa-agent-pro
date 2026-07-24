"""Convert a TestSuite into a professional XLSX workbook using XlsxWriter."""

from __future__ import annotations

import logging
import math
import tempfile
import time
from pathlib import Path

import xlsxwriter

from tools.cell_sanitizer import sanitize_cell
from tools.models import TestSuite
from tools.secure_temp import SUBDIR_NAME, make_secure_temp_path

logger = logging.getLogger(__name__)

# One row per test case — steps and expected results joined with newlines.
# Requirement ID / Risk Score / Risk Label / Risk Rationale / Stable ID are
# intentionally NOT exported here — they're internal fields (RTM traceability,
# risk_scorer sorting, dedup and TestRail push all still read them off the
# TestCase model), just not shown as columns in the tester-facing file.
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
_TOTAL_COLS = 11

_HEADERS = [
    "TC ID",
    "Module",
    "Title",
    "Priority",
    "Type",
    "Preconditions",
    "Steps / Actions",
    "Test Data",
    "Expected Results",
    "Status",
    "Notes",
]

_COL_WIDTHS = [10, 18, 30, 12, 14, 28, 45, 28, 45, 12, 20]

# Excel's default row height (points) for 11pt Calibri; one wrapped display
# line occupies roughly this much vertical space.
_LINE_HEIGHT_PT = 15
_MIN_ROW_HEIGHT = 40


def _row_height_for(cells: list[tuple[str, float]]) -> float:
    """Return a row height (points) that fits the tallest cell in the row.

    Each ``cells`` entry is ``(text, column_width)``. A cell's display-line
    count is its explicit newlines plus the extra lines Excel adds when a
    logical line is wider than the column (text wrapping). The row height is
    the largest cell line count times ``_LINE_HEIGHT_PT``, floored at
    ``_MIN_ROW_HEIGHT`` so short rows stay comfortable. Column widths are kept
    fixed (``_COL_WIDTHS``); only the height adapts.
    """
    max_lines = 1
    for text, col_width in cells:
        width_chars = max(1, int(col_width))
        lines = 0
        for logical in str(text).split("\n"):
            lines += max(1, math.ceil(len(logical) / width_chars))
        max_lines = max(max_lines, lines)
    return max(_MIN_ROW_HEIGHT, max_lines * _LINE_HEIGHT_PT)


def generate_test_case_xlsx(suite: TestSuite, output_path: str | None = None) -> str:
    """Write suite to an XLSX file and return the file path."""
    if output_path is None:
        output_path = make_secure_temp_path(prefix="qa_test_cases_", suffix=".xlsx")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    workbook = xlsxwriter.Workbook(output_path, {"strings_to_formulas": False})
    try:
        _write_workbook(workbook, suite)
    finally:
        workbook.close()

    logger.info("XLSX written: %s (%d test cases)", output_path, len(suite.test_cases))
    return output_path


def cleanup_temp_files(max_age_seconds: int = 3600) -> int:
    """Delete qa_test_cases_*.xlsx temp files older than max_age_seconds. Returns count deleted."""
    tmp_dir = Path(tempfile.gettempdir())
    now = time.time()
    deleted = 0
    # Sweep both the secure export subdir (new location) and the tempdir root
    # (legacy pre-QW-18 files) so nothing is orphaned.
    for base in (tmp_dir / SUBDIR_NAME, tmp_dir):
        for path in base.glob("qa_test_cases_*.xlsx"):
            try:
                age = now - path.stat().st_mtime
                if age > max_age_seconds:
                    path.unlink(missing_ok=True)
                    deleted += 1
                    logger.info(
                        "Cleaned up stale XLSX temp file: %s (age %.0fs)", path, age
                    )
            except OSError:
                logger.warning("Could not check/delete temp file: %s", path)
    if deleted:
        logger.info("XLSX cleanup: removed %d stale file(s)", deleted)
    return deleted


def _write_workbook(workbook: xlsxwriter.Workbook, suite: TestSuite) -> None:
    # ------------------------------------------------------------------ formats
    header_fmt = workbook.add_format(
        {
            "bold": True,
            "font_color": "#FFFFFF",
            "bg_color": "#1F4E79",
            "border": 1,
            "align": "center",
            "valign": "vcenter",
            "text_wrap": True,
        }
    )

    def _cell_fmt(bg: str) -> xlsxwriter.format.Format:
        return workbook.add_format(
            {
                "bg_color": bg,
                "border": 1,
                "valign": "top",
                "text_wrap": True,
            }
        )

    even_fmt = _cell_fmt("#FFFFFF")
    odd_fmt = _cell_fmt("#EBF3FB")

    fmt_critical = workbook.add_format(
        {
            "bg_color": "#FF4444",
            "font_color": "#FFFFFF",
            "border": 1,
            "bold": True,
            "valign": "top",
        }
    )
    fmt_high = workbook.add_format(
        {"bg_color": "#FFC7CE", "font_color": "#9C0006", "border": 1, "valign": "top"}
    )
    fmt_medium = workbook.add_format(
        {"bg_color": "#FFEB9C", "font_color": "#9C6500", "border": 1, "valign": "top"}
    )
    fmt_low = workbook.add_format(
        {"bg_color": "#C6EFCE", "font_color": "#006100", "border": 1, "valign": "top"}
    )

    # Direct (baked-in) fills for the Priority column. Apple Numbers drops Excel
    # *conditional* formatting on import, so the value-based colors defined above
    # would vanish there. Writing the color straight onto the cell makes it show
    # in Numbers too. The conditional_format rules below are kept, so Excel /
    # LibreOffice / Google Sheets still re-color live when a Priority is changed
    # (a conditional format overrides the direct fill in those apps).
    _pri_fill = {
        "Critical": workbook.add_format(
            {
                "bg_color": "#FF4444",
                "font_color": "#FFFFFF",
                "border": 1,
                "bold": True,
                "valign": "top",
                "text_wrap": True,
            }
        ),
        "High": workbook.add_format(
            {
                "bg_color": "#FFC7CE",
                "font_color": "#9C0006",
                "border": 1,
                "valign": "top",
                "text_wrap": True,
            }
        ),
        "Medium": workbook.add_format(
            {
                "bg_color": "#FFEB9C",
                "font_color": "#9C6500",
                "border": 1,
                "valign": "top",
                "text_wrap": True,
            }
        ),
        "Low": workbook.add_format(
            {
                "bg_color": "#C6EFCE",
                "font_color": "#006100",
                "border": 1,
                "valign": "top",
                "text_wrap": True,
            }
        ),
    }

    # ------------------------------------------------------------------ sheet 1
    ws = workbook.add_worksheet("Test Cases")

    for i, w in enumerate(_COL_WIDTHS):
        ws.set_column(i, i, w)

    ws.freeze_panes(1, 0)
    ws.autofilter(0, 0, 0, _TOTAL_COLS - 1)
    ws.write_row(0, 0, _HEADERS, header_fmt)
    ws.set_row(0, 22)

    # Present rows in TC-ID order. The agent already assigns TC-IDs in final
    # risk order (highest-risk = TC-001), so sorting by TC-ID keeps the sheet's
    # row order identical to the IDs — never re-sort by priority/type here, or the
    # visible IDs would no longer be sequential (that was a reported bug).
    def _tc_id_key(tc: object) -> int:
        digits = "".join(ch for ch in (getattr(tc, "tc_id", "") or "") if ch.isdigit())
        return int(digits) if digits else 0

    sorted_cases = sorted(suite.test_cases, key=_tc_id_key)

    # Status column needs its own base formats: centered + no bg so conditional format colors show
    status_odd_fmt = workbook.add_format(
        {
            "bg_color": "#D9D9D9",
            "font_color": "#595959",
            "border": 1,
            "align": "center",
            "valign": "vcenter",
        }
    )
    status_even_fmt = workbook.add_format(
        {
            "bg_color": "#D9D9D9",
            "font_color": "#595959",
            "border": 1,
            "align": "center",
            "valign": "vcenter",
        }
    )

    for row_idx, tc in enumerate(sorted_cases, start=1):
        fmt = odd_fmt if row_idx % 2 == 1 else even_fmt
        status_fmt = status_odd_fmt if row_idx % 2 == 1 else status_even_fmt

        # Combine steps into a single multi-line string: "1. action\n2. action"
        # Wrapped in sanitize_cell() -- this text originates from LLM-generated or
        # Jira-derived content and must not be interpreted as a spreadsheet formula.
        steps_text = sanitize_cell(
            "\n".join(f"{s.step_number}. {s.action}" for s in tc.steps)
        )

        # Combine expected results: "1. result\n2. result"
        expected_text = sanitize_cell(
            "\n".join(f"{s.step_number}. {s.expected_result}" for s in tc.steps)
        )

        # Combine test data only for steps that have it: "Step N: data"
        data_lines = [
            f"Step {s.step_number}: {s.test_data}" for s in tc.steps if s.test_data
        ]
        test_data_text = sanitize_cell("\n".join(data_lines))

        ws.write(row_idx, _COL_TCID, tc.tc_id, fmt)
        ws.write(row_idx, _COL_MODULE, sanitize_cell(tc.module), fmt)
        ws.write(row_idx, _COL_TITLE, sanitize_cell(tc.title), fmt)
        ws.write(
            row_idx,
            _COL_PRIORITY,
            tc.priority.value,
            _pri_fill.get(tc.priority.value, fmt),
        )
        ws.write(row_idx, _COL_TYPE, tc.type.value, fmt)
        ws.write(row_idx, _COL_PRECOND, sanitize_cell(tc.preconditions or ""), fmt)
        ws.write(row_idx, _COL_STEPS, steps_text, fmt)
        ws.write(row_idx, _COL_TESTDATA, test_data_text, fmt)
        ws.write(row_idx, _COL_EXPECTED, expected_text, fmt)
        ws.write(row_idx, _COL_STATUS, "Not Run", status_fmt)
        ws.write(row_idx, _COL_NOTES, "", fmt)

        # Row height: fit the tallest cell in the row (wrapped text included),
        # not just the step count -- long titles/preconditions/data/expected
        # results no longer clip. Column widths stay fixed (_COL_WIDTHS).
        row_cells = [
            (tc.tc_id, _COL_WIDTHS[_COL_TCID]),
            (tc.module, _COL_WIDTHS[_COL_MODULE]),
            (tc.title, _COL_WIDTHS[_COL_TITLE]),
            (tc.priority.value, _COL_WIDTHS[_COL_PRIORITY]),
            (tc.type.value, _COL_WIDTHS[_COL_TYPE]),
            (tc.preconditions or "", _COL_WIDTHS[_COL_PRECOND]),
            (steps_text, _COL_WIDTHS[_COL_STEPS]),
            (test_data_text, _COL_WIDTHS[_COL_TESTDATA]),
            (expected_text, _COL_WIDTHS[_COL_EXPECTED]),
        ]
        ws.set_row(row_idx, _row_height_for(row_cells))

    last_data_row = len(suite.test_cases) + 1

    # Conditional format on Priority column (D)
    pri_range = f"D2:D{last_data_row}"
    ws.conditional_format(
        pri_range,
        {
            "type": "cell",
            "criteria": "equal to",
            "value": '"Critical"',
            "format": fmt_critical,
        },
    )
    ws.conditional_format(
        pri_range,
        {"type": "cell", "criteria": "equal to", "value": '"High"', "format": fmt_high},
    )
    ws.conditional_format(
        pri_range,
        {
            "type": "cell",
            "criteria": "equal to",
            "value": '"Medium"',
            "format": fmt_medium,
        },
    )
    ws.conditional_format(
        pri_range,
        {"type": "cell", "criteria": "equal to", "value": '"Low"', "format": fmt_low},
    )

    # Status column: color-coded conditional formatting (no align — Excel ignores it in cond. formats)
    _status_base = {"border": 1, "align": "center", "valign": "vcenter"}
    fmt_st_pass = workbook.add_format(
        {**_status_base, "bg_color": "#C6EFCE", "font_color": "#006100", "bold": True}
    )
    fmt_st_fail = workbook.add_format(
        {**_status_base, "bg_color": "#FFC7CE", "font_color": "#9C0006", "bold": True}
    )
    fmt_st_blocked = workbook.add_format(
        {**_status_base, "bg_color": "#FFEB9C", "font_color": "#9C6500", "bold": True}
    )
    fmt_st_notrun = workbook.add_format(
        {**_status_base, "bg_color": "#D9D9D9", "font_color": "#595959"}
    )
    fmt_st_inprog = workbook.add_format(
        {**_status_base, "bg_color": "#BDD7EE", "font_color": "#1F4E79", "bold": True}
    )
    fmt_st_skipped = workbook.add_format(
        {**_status_base, "bg_color": "#E2EFDA", "font_color": "#375623"}
    )

    status_col_range = f"J2:J{last_data_row}"
    ws.conditional_format(
        status_col_range,
        {
            "type": "cell",
            "criteria": "equal to",
            "value": '"Pass"',
            "format": fmt_st_pass,
        },
    )
    ws.conditional_format(
        status_col_range,
        {
            "type": "cell",
            "criteria": "equal to",
            "value": '"Fail"',
            "format": fmt_st_fail,
        },
    )
    ws.conditional_format(
        status_col_range,
        {
            "type": "cell",
            "criteria": "equal to",
            "value": '"Blocked"',
            "format": fmt_st_blocked,
        },
    )
    ws.conditional_format(
        status_col_range,
        {
            "type": "cell",
            "criteria": "equal to",
            "value": '"Not Run"',
            "format": fmt_st_notrun,
        },
    )
    ws.conditional_format(
        status_col_range,
        {
            "type": "cell",
            "criteria": "equal to",
            "value": '"In Progress"',
            "format": fmt_st_inprog,
        },
    )
    ws.conditional_format(
        status_col_range,
        {
            "type": "cell",
            "criteria": "equal to",
            "value": '"Skipped"',
            "format": fmt_st_skipped,
        },
    )

    # Status dropdown with tooltip indicator
    ws.data_validation(
        f"J2:J{last_data_row}",
        {
            "validate": "list",
            "source": ["Not Run", "Pass", "Fail", "Blocked", "In Progress", "Skipped"],
            "input_title": "Select Status",
            "input_message": "Choose a test status from the list",
        },
    )

    # ------------------------------------------------------------------ sheet 2: Summary
    summary_ws = workbook.add_worksheet("Summary")
    summary_ws.set_column("A:A", 25)
    summary_ws.set_column("B:B", 15)

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

    summary_ws.write("A1", "Test Execution Summary", title_fmt)
    summary_ws.set_row(0, 30)

    total = len(suite.test_cases)
    status_range = f"'Test Cases'!J2:J{last_data_row}"
    summary_rows = [
        (
            "Total Test Cases",
            f"=COUNTA('Test Cases'!A2:A{last_data_row})",
            value_fmt,
            total,
        ),
        ("Pass", f'=COUNTIF({status_range},"Pass")', value_fmt, 0),
        ("Fail", f'=COUNTIF({status_range},"Fail")', value_fmt, 0),
        ("Blocked", f'=COUNTIF({status_range},"Blocked")', value_fmt, 0),
        ("Not Run", f'=COUNTIF({status_range},"Not Run")', value_fmt, total),
        ("In Progress", f'=COUNTIF({status_range},"In Progress")', value_fmt, 0),
        ("Skipped", f'=COUNTIF({status_range},"Skipped")', value_fmt, 0),
        ("Pass Rate", "=IFERROR(B4/B3,0)", pct_fmt, 0.0),
    ]
    for i, (label, formula, vfmt, cached) in enumerate(summary_rows, start=2):
        summary_ws.write(i, 0, label, label_fmt)
        summary_ws.write_formula(i, 1, formula, vfmt, cached)

    summary_ws.write("A11", "Priority", label_fmt)
    summary_ws.write("B11", "Count", label_fmt)
    for j, pri in enumerate(["Critical", "High", "Medium", "Low"], start=12):
        count = sum(1 for tc in suite.test_cases if tc.priority.value == pri)
        summary_ws.write(j, 0, pri, label_fmt)
        summary_ws.write(j, 1, count, value_fmt)

    summary_ws.write("A17", "Type", label_fmt)
    summary_ws.write("B17", "Count", label_fmt)
    for j, ttype in enumerate(
        [
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
        ],
        start=18,
    ):
        count = sum(1 for tc in suite.test_cases if tc.type.value == ttype)
        summary_ws.write(j, 0, ttype, label_fmt)
        summary_ws.write(j, 1, count, value_fmt)
