"""Convert a TestSuite into a professional XLSX workbook using XlsxWriter."""

from __future__ import annotations

import logging
import math
import tempfile
import time
from pathlib import Path

import xlsxwriter

from tools.bilingual import bidi_isolate, is_rtl_cell
from tools.cell_sanitizer import sanitize_cell
from tools.models import TestSuite, format_test_data_lines
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
# F6: APPENDED, never inserted. Every hardcoded column letter below (D for
# Priority, J for Status, A for TC ID) is derived from these indices via
# _col_letter(), so a future insert cannot leave a stale letter behind.
_COL_CATEGORY = 11
_TOTAL_COLS = 12


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
]

# Index-parallel to _HEADERS (test_column_constants_stay_in_lockstep) and also
# the row-height autofit input. Column L went 22 -> 24: "Coverage Category" is 17
# chars and the widest value, "Negative / Error Flows", is 22.
_COL_WIDTHS = [10, 18, 30, 12, 14, 28, 45, 28, 45, 12, 20, 24]


def _prepare(text: str) -> str:
    """Security transform first, then bidi presentation.

    ``sanitize_cell`` runs BEFORE ``bidi_isolate`` so the
    formula-injection neutraliser still sees the real first character; the
    RLM/LRM marks are then inserted around each Arabic run. The Unicode
    Bidirectional Algorithm reorders neutral characters (quotes, colons,
    parentheses) by surrounding direction, so an Arabic string quoted inside
    an English sentence -- AR: "..." -- otherwise renders with its closing
    quote in the wrong place. A no-op (byte-identical) for text containing
    no Arabic, which is why the workbook is unchanged for a non-bilingual
    suite. Never raises."""
    try:
        return bidi_isolate(sanitize_cell(text or ""))
    except Exception:  # pragma: no cover - defensive
        logger.debug("cell preparation failed", exc_info=True)
        return sanitize_cell(text or "")


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


def _notes_cell(tc: object, rule_pack_note: str) -> str:
    """Text for the Notes column of one case.

    tools/risk_scorer.py scores EVERY suite and the sheet's row order IS the risk
    order (TC-001 = highest risk), but risk_label / risk_score were never
    exported -- while Notes was empty in 65/65 rows of the 2026-07-30 run,
    because it only ever carried a Batch-3 rule-pack note and the rule packs are
    off in that deployment. Unconditional since 2026-08-12 (QA_XLSX_RISK_NOTES
    was deleted).

    A rule-pack note ALWAYS wins, so no information is ever displaced, and any
    problem degrades to the rule-pack note -- i.e. exactly the pre-feature
    output. Pure and never raises.

    2026-08-04: the cell used to read "Risk: <label> (<score>)", and the LABEL
    duplicated the Priority column -- contradicting it in 10/97 rows of that
    day's run (Priority=High vs "Risk: CRITICAL (34)"), because the label mixes
    in type weight and Priority does not. Only the SCORE is written now: it is
    the sheet's row-order key and appears in no other column.
    """
    note = rule_pack_note or ""
    if note:
        return note
    try:
        # The gate stays on risk_label: an UNSCORED suite has risk_label == ""
        # and risk_score == 0, and must keep producing an empty Notes cell rather
        # than a meaningless "Risk 0".
        label = str(getattr(tc, "risk_label", "") or "").strip()
        if not label:
            return note
        score = getattr(tc, "risk_score", None)
        # No score means nothing is left to show once the label is dropped, so
        # degrade to the rule-pack note (empty in practice) rather than invent one.
        return f"Risk {score}" if score is not None else note
    except Exception:  # pragma: no cover - defensive
        logger.debug("risk note rendering failed -- Notes left as-is", exc_info=True)
        return note


def generate_test_case_xlsx(suite: TestSuite, output_path: str | None = None) -> str:
    """Write suite to an XLSX file and return the file path."""
    if output_path is None:
        output_path = make_secure_temp_path(prefix="qa_test_cases_", suffix=".xlsx")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    workbook = xlsxwriter.Workbook(output_path, {"strings_to_formulas": False})
    try:
        _write_workbook(workbook, suite)
        _write_report_sheets(workbook, suite)
        _write_checklist_sheets(workbook, suite)
        _write_assumed_sheet(workbook, suite)
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


def _write_checklist_sheets(workbook: xlsxwriter.Workbook, suite: TestSuite) -> None:
    """Append the 'Requirements Checklist' and 'Coverage Audit' sheets when the
    suite carries ``_checklist_artifacts`` (QA_ATOMIC_CHECKLIST_ENABLED).

    No-op when absent, so the workbook is byte-identical on the flag-off path.
    Never raises — a failure here must never break the core workbook. The
    checklist is a DURABLE artifact, which is why it gets its own sheet rather
    than a note in the summary."""
    artifacts = getattr(suite, "_checklist_artifacts", None)
    if not artifacts:
        return
    try:
        from tools.atomic_checklist import (
            checklist_from_dicts,
            checklist_rows,
            coverage_rows,
        )

        items = checklist_from_dicts(artifacts.get("items") or [])
        coverage = artifacts.get("coverage") or {}
        audit = artifacts.get("audit") or {}
        header_fmt = workbook.add_format(
            {
                "bold": True,
                "font_color": "#FFFFFF",
                "bg_color": "#1F4E79",
                "border": 1,
                "valign": "vcenter",
                "text_wrap": True,
            }
        )
        cell_fmt = workbook.add_format(
            {"border": 1, "valign": "top", "text_wrap": True}
        )
        for name, rows in (
            ("Requirements Checklist", checklist_rows(items, coverage)),
            ("Coverage Audit", coverage_rows(coverage, audit, items)),
        ):
            if not rows:
                continue
            try:
                ws = workbook.add_worksheet(name)
                ws.set_column(0, 0, 14)
                ws.set_column(1, max(1, len(rows[0]) - 1), 42)
                for r, row in enumerate(rows):
                    fmt = header_fmt if r == 0 else cell_fmt
                    for c, value in enumerate(row):
                        ws.write(r, c, sanitize_cell(str(value)), fmt)
            except Exception:
                logger.warning(
                    "Failed writing the %s sheet — skipping it", name, exc_info=True
                )
    except Exception:
        logger.warning("checklist-sheet generation failed — skipping", exc_info=True)


def _write_assumed_sheet(workbook: xlsxwriter.Workbook, suite: TestSuite) -> None:
    """Write the "Assumed Requirements" sheet, if an entailment review produced one.

    These cases assert behaviour the ticket never states. They are kept -- one may
    be a real requirement nobody wrote down -- but off the executable sheet, where
    they would file defects against a team that never agreed to the behaviour.

    No-op when absent, so the workbook is byte-identical on the flag-off path.
    Never raises: a failure here must never break the core workbook.
    """
    artifacts = getattr(suite, "_assumed_artifacts", None)
    if not artifacts:
        return
    try:
        rows = artifacts.get("rows") or []
        if not rows:
            return
        header_fmt = workbook.add_format(
            {
                "bold": True,
                "font_color": "#FFFFFF",
                "bg_color": "#8B5E00",
                "border": 1,
                "valign": "vcenter",
                "text_wrap": True,
            }
        )
        cell_fmt = workbook.add_format(
            {"border": 1, "valign": "top", "text_wrap": True}
        )
        ws = workbook.add_worksheet("Assumed Requirements")
        ws.set_column(0, 1, 14)
        ws.set_column(2, max(2, len(rows[0]) - 1), 42)
        for r, row in enumerate(rows):
            fmt = header_fmt if r == 0 else cell_fmt
            for c, value in enumerate(row):
                ws.write(r, c, sanitize_cell(str(value)), fmt)
    except Exception:
        logger.warning(
            "Failed writing the Assumed Requirements sheet - skipping it",
            exc_info=True,
        )


def _write_report_sheets(workbook: xlsxwriter.Workbook, suite: TestSuite) -> None:
    """Append 'AC Validation' and 'Test Plan' sheets when the suite carries
    report_artifacts (QA_TEST_PLAN_ARTIFACTS). No-op when absent. Never raises —
    a failure here must never break the core workbook."""
    artifacts = getattr(suite, "_report_artifacts", None)
    if not artifacts:
        return
    try:
        from tools.test_plan_report import ac_validation_rows, plan_rows

        header_fmt = workbook.add_format(
            {
                "bold": True,
                "font_color": "#FFFFFF",
                "bg_color": "#1F4E79",
                "border": 1,
                "valign": "vcenter",
                "text_wrap": True,
            }
        )
        cell_fmt = workbook.add_format(
            {"border": 1, "valign": "top", "text_wrap": True}
        )
        for name, rows in (
            ("AC Validation", ac_validation_rows(artifacts)),
            ("Test Plan", plan_rows(artifacts)),
        ):
            if not rows:
                continue
            try:
                ws = workbook.add_worksheet(name)
                ws.set_column(0, 0, 22)
                ws.set_column(1, max(1, len(rows[0]) - 1), 45)
                for r, row in enumerate(rows):
                    fmt = header_fmt if r == 0 else cell_fmt
                    for c, value in enumerate(row):
                        ws.write(r, c, sanitize_cell(str(value)), fmt)
            except Exception:
                logger.warning(
                    "Failed writing the %s sheet — skipping it", name, exc_info=True
                )
    except Exception:
        logger.warning("report-sheet generation failed — skipping", exc_info=True)


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

    def _cell_fmt(bg: str, rtl: bool = False) -> xlsxwriter.format.Format:
        props = {
            "bg_color": bg,
            "border": 1,
            "valign": "top",
            "text_wrap": True,
        }
        if rtl:
            # xlsxwriter's documented `reading_order` format property emits
            # readingOrder="2" (plus horizontal="right") into the cell
            # alignment element of xl/styles.xml. Without it Excel lays an
            # Arabic-majority cell out left-to-right even though the string
            # itself is correct, and the tester blames the generator. No
            # monkeypatch or OOXML post-patching is needed -- verified
            # against xlsxwriter 3.2.9 and asserted at the OOXML level (not
            # by visual rendering) in tests/test_bilingual_rules.py.
            props["reading_order"] = 2
            props["align"] = "right"
        return workbook.add_format(props)

    even_fmt = _cell_fmt("#FFFFFF")
    odd_fmt = _cell_fmt("#EBF3FB")
    even_rtl_fmt = _cell_fmt("#FFFFFF", rtl=True)
    odd_rtl_fmt = _cell_fmt("#EBF3FB", rtl=True)

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

    # Notes for the Notes column: the Batch 3 standing-rules pack attaches
    # a mechanical assumption / clarification label per tc_id. Absent =>
    # every Notes cell stays empty, exactly as before.
    rule_pack_notes = getattr(suite, "_rule_pack_notes", None) or {}

    for row_idx, tc in enumerate(sorted_cases, start=1):
        fmt = odd_fmt if row_idx % 2 == 1 else even_fmt
        rtl_fmt = odd_rtl_fmt if row_idx % 2 == 1 else even_rtl_fmt
        status_fmt = status_odd_fmt if row_idx % 2 == 1 else status_even_fmt

        def _write_text(col: int, text: str, fmt=fmt, rtl_fmt=rtl_fmt) -> None:
            """One text cell: sanitised, bidi-isolated, and RTL-formatted
            when the content is Arabic-majority. Applied to EVERY text column
            (Module, Title, Preconditions, Steps, Test Data, Expected
            Results, Notes) -- an Arabic message can legitimately land in any
            of them, and a cell that is right-to-left in one column and
            left-to-right in the next reads as a rendering bug."""
            value = _prepare(text)
            ws.write(row_idx, col, value, rtl_fmt if is_rtl_cell(text or "") else fmt)

        # Combine steps into a single multi-line string: "1. action\n2. action"
        # Wrapped in sanitize_cell() -- this text originates from LLM-generated or
        # Jira-derived content and must not be interpreted as a spreadsheet formula.
        steps_text = _prepare(
            "\n".join(f"{s.step_number}. {s.action}" for s in tc.steps)
        )

        # Combine expected results: "1. result\n2. result"
        expected_text = _prepare(
            "\n".join(f"{s.step_number}. {s.expected_result}" for s in tc.steps)
        )

        # Combine test data only for steps that have it: "Step N: data"
        data_lines = [
            f"Step {s.step_number}: {s.test_data}" for s in tc.steps if s.test_data
        ]
        # Case-level data-provisioning plan. Only present when the case declared
        # test_data; appended after the per-step lines so a case with none renders
        # byte-identically to before.
        data_lines.extend(format_test_data_lines(tc.test_data))
        test_data_text = _prepare("\n".join(data_lines))

        ws.write(row_idx, _COL_TCID, tc.tc_id, fmt)
        _write_text(_COL_MODULE, tc.module)
        _write_text(_COL_TITLE, tc.title)
        ws.write(
            row_idx,
            _COL_PRIORITY,
            tc.priority.value,
            _pri_fill.get(tc.priority.value, fmt),
        )
        ws.write(row_idx, _COL_TYPE, tc.type.value, fmt)
        _write_text(_COL_PRECOND, tc.preconditions or "")
        ws.write(
            row_idx,
            _COL_STEPS,
            steps_text,
            rtl_fmt if is_rtl_cell(steps_text) else fmt,
        )
        ws.write(
            row_idx,
            _COL_TESTDATA,
            test_data_text,
            rtl_fmt if is_rtl_cell(test_data_text) else fmt,
        )
        ws.write(
            row_idx,
            _COL_EXPECTED,
            expected_text,
            rtl_fmt if is_rtl_cell(expected_text) else fmt,
        )
        ws.write(row_idx, _COL_STATUS, "Not Run", status_fmt)
        notes_text = _notes_cell(tc, rule_pack_notes.get(tc.tc_id, ""))
        _write_text(_COL_NOTES, notes_text)
        # F6: which of the 8 generation categories produced this case. Empty when
        # it could not be resolved -- never guessed. A value self-reported by the
        # host model is normalised before it reaches here.
        _write_text(_COL_CATEGORY, getattr(tc, "category", None) or "")

        # Row height: fit the tallest cell in the row (wrapped text included),
        # not just the step count -- long titles/preconditions/data/expected
        # results no longer clip. Column widths stay fixed (_COL_WIDTHS).
        row_cells = [
            (notes_text, _COL_WIDTHS[_COL_NOTES]),
            (getattr(tc, "category", None) or "", _COL_WIDTHS[_COL_CATEGORY]),
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

    # Conditional format on the Priority column, letter DERIVED from the index.
    _pri = _col_letter(_COL_PRIORITY)
    pri_range = f"{_pri}2:{_pri}{last_data_row}"
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

    _stat = _col_letter(_COL_STATUS)
    status_col_range = f"{_stat}2:{_stat}{last_data_row}"
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
        f"{_col_letter(_COL_STATUS)}2:{_col_letter(_COL_STATUS)}{last_data_row}",
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
    _s = _col_letter(_COL_STATUS)
    status_range = f"'Test Cases'!{_s}2:{_s}{last_data_row}"
    summary_rows = [
        (
            "Total Test Cases",
            f"=COUNTA('Test Cases'!{_col_letter(_COL_TCID)}2:"
            f"{_col_letter(_COL_TCID)}{last_data_row})",
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
    # 2026-07-30 run review, item 5: the Priority and Type blocks were HARDCODED
    # literals while the Status block above was COUNTIF, so the moment a tester
    # edited, added or deleted a row the SAME sheet contradicted itself. Same
    # shape as summary_rows: a real formula plus the computed count as the CACHED
    # value, so a viewer that ignores formulas still shows the right number.
    # Column letters are DERIVED from the _COL_* indices, never hardcoded.
    _pri_col = _col_letter(_COL_PRIORITY)
    priority_range = f"'Test Cases'!{_pri_col}2:{_pri_col}{last_data_row}"
    for j, pri in enumerate(["Critical", "High", "Medium", "Low"], start=12):
        count = sum(1 for tc in suite.test_cases if tc.priority.value == pri)
        summary_ws.write(j, 0, pri, label_fmt)
        summary_ws.write_formula(
            j, 1, f'=COUNTIF({priority_range},"{pri}")', value_fmt, count
        )

    _type_col = _col_letter(_COL_TYPE)
    type_range = f"'Test Cases'!{_type_col}2:{_type_col}{last_data_row}"
    summary_ws.write("A17", "Test Type", label_fmt)
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
        summary_ws.write_formula(
            j, 1, f'=COUNTIF({type_range},"{ttype}")', value_fmt, count
        )
