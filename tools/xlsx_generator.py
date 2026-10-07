"""Convert a TestSuite into a professional XLSX workbook using XlsxWriter."""

from __future__ import annotations

import logging
import math
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

import xlsxwriter

from tools.bilingual import bidi_isolate, is_rtl_cell
from tools.cell_sanitizer import sanitize_cell
from tools.models import (
    TestSuite,
    display_requirement_id,
    format_test_data_lines,
)
from tools.secure_temp import SUBDIR_NAME, make_secure_temp_path

logger = logging.getLogger(__name__)

# One row per test case — steps and expected results joined with newlines.
# Risk Score / Risk Label / Risk Rationale / Stable ID are intentionally NOT
# exported here — they're internal fields (risk_scorer sorting, dedup and the
# TestRail push all still read them off the TestCase model), just not shown as
# columns in the tester-facing file.
#
# `requirement_id` LEFT that list on 2026-08-19 (F06). It was a defensible
# "internal field" while it was only a sort/dedup key; it stopped being one when
# the finalize reply started claiming "N/N acceptance criteria traced", because
# the evidence for that claim then lived only in data/suites.db and the person
# holding the workbook could not check it. It is column M, and the Requirements
# Traceability sheet gives it the per-AC counts.
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
# F06 (2026-08-19): APPENDED for the same reason _COL_CATEGORY was, never
# inserted -- every hardcoded column letter is derived from these indices.
_COL_REQUIREMENT = 12
# v1.97.0 cursor-hardening (item 12): APPENDED, never inserted -- same
# reasoning as _COL_CATEGORY/_COL_REQUIREMENT above. Sortable/filterable risk
# score, replacing the "Risk <score>" text that used to be glued into Notes.
_COL_RISK_SCORE = 13
_TOTAL_COLS = 14


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
    "Requirement ID",
    "Risk Score",
]

# Index-parallel to _HEADERS (test_column_constants_stay_in_lockstep) and also
# the row-height autofit input. Column L went 22 -> 24: "Coverage Category" is 17
# chars and the widest value, "Negative / Error Flows", is 22.
_COL_WIDTHS = [10, 18, 30, 12, 14, 28, 45, 28, 45, 12, 20, 24, 16, 11]


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

    v1.97.0 cursor-hardening (item 12): the risk score used to be glued into
    this cell as "Risk <score>" text (2026-08-04 - 2026-09-26), making it
    unsortable and unfilterable and duplicating nothing else on the row. The
    score now has its own column (_risk_score_cell / _COL_RISK_SCORE below);
    this cell carries ONLY the rule-pack note, exactly the pre-2026-08-04
    output. Pure and never raises.
    """
    return rule_pack_note or ""


def _risk_score_cell(tc: object) -> int | None:
    """The Risk Score cell for one case: the integer score, or None when the
    suite was never scored.

    Mirrors _notes_cell's old gate exactly: an UNSCORED suite has
    risk_label == "" and risk_score == 0, and must produce an EMPTY cell
    (write_blank), not a misleading "0" that reads as "scored and safe".
    Pure and never raises.
    """
    try:
        label = str(getattr(tc, "risk_label", "") or "").strip()
        if not label:
            return None
        score = getattr(tc, "risk_score", None)
        return int(score) if score is not None else None
    except Exception:  # pragma: no cover - defensive
        logger.debug("risk score cell rendering failed -- left blank", exc_info=True)
        return None


_UNTRACED_LABEL = "(untraced)"


def _requirement_cell(tc: object) -> str:
    """The Requirement ID cell for one case: the canonical tag, or _UNTRACED_LABEL.

    Canonicalised through tools.models.display_requirement_id so this column and
    the Requirements Traceability sheet name the same criterion identically.

    An untagged case gets the LITERAL "(untraced)" rather than a blank: a reader
    cannot tell an empty spreadsheet cell from a lost value or a broken export,
    and "this case traces to no requirement" is a fact worth stating -- the one
    F04 will make routine. Pure and never raises."""
    try:
        return (
            display_requirement_id(getattr(tc, "requirement_id", "")) or _UNTRACED_LABEL
        )
    except Exception:  # pragma: no cover - defensive
        logger.debug("requirement cell rendering failed", exc_info=True)
        return _UNTRACED_LABEL


def generate_test_case_xlsx(suite: TestSuite, output_path: str | None = None) -> str:
    """Write suite to an XLSX file and return the file path."""
    if output_path is None:
        output_path = make_secure_temp_path(prefix="qa_test_cases_", suffix=".xlsx")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

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


def _sheet_formats(
    workbook: xlsxwriter.Workbook, header_bg: str
) -> tuple[xlsxwriter.format.Format, xlsxwriter.format.Format]:
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
    return header_fmt, cell_fmt


def _write_grid(ws: object, rows: list, header_fmt: object, cell_fmt: object) -> None:
    """Write ``rows`` from A1, row 0 in the header format, every cell sanitised."""
    for r, row in enumerate(rows):
        fmt = header_fmt if r == 0 else cell_fmt
        for c, value in enumerate(row):
            ws.write(r, c, sanitize_cell(str(value)), fmt)


def _write_checklist_sheets(workbook: xlsxwriter.Workbook, suite: TestSuite) -> None:
    """Append the 'Requirements Checklist' sheet when the suite carries
    ``_checklist_artifacts`` (unconditional since 2026-08-14 --
    QA_ATOMIC_CHECKLIST_ENABLED was deleted and the checklist hardcoded ON).

    A plain list of the parsed requirements.

    No-op when absent, so the workbook is byte-identical for a suite that
    carried no checklist.
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
        )

        items = checklist_from_dicts(artifacts.get("items") or [])
        header_fmt, cell_fmt = _sheet_formats(workbook, "#1F4E79")
        for name, rows in (("Requirements Checklist", checklist_rows(items)),):
            if not rows:
                continue
            try:
                ws = workbook.add_worksheet(name)
                ws.set_column(0, 0, 14)
                ws.set_column(1, max(1, len(rows[0]) - 1), 42)
                _write_grid(ws, rows, header_fmt, cell_fmt)
            except Exception:
                logger.warning(
                    "Failed writing the %s sheet — skipping it", name, exc_info=True
                )
    except Exception:
        logger.warning("checklist-sheet generation failed — skipping", exc_info=True)


def _write_rtm_sheet(workbook: xlsxwriter.Workbook, suite: TestSuite) -> None:
    """Append the 'Requirements Traceability' sheet when the suite carries
    ``_rtm_artifacts`` (attached in agents.test_scenario_agent._finalize_generation).

    F06 (2026-08-19): the finalize reply claims "N/N acceptance criteria traced";
    until this sheet existed that claim was checkable only against
    data/suites.db. One row per criterion WITH the case count, so an
    over-weighted criterion -- 25 of 96 cases on AC-001 against 3 on AC-002 in
    the 2026-08-16 run -- is legible instead of hidden behind a total.

    Written AHEAD of 'Requirements Checklist' deliberately: that sheet links
    `CL-*` ids, the model-derived atomic checklist, which is a different
    denominator with a different provenance. This one is about the `AC-*` ids the
    headline claim is actually made against.

    NO-OP WHEN THE SUITE CARRIES NO ARTIFACTS, which includes every re-export
    through qa_export_suite: suite_store.load_suite rebuilds a TestSuite from the
    `cases` table alone, so no private attribute survives and the checklist /
    report / assumed / generation-notes sheets are all absent there too. The
    Requirement ID COLUMN is unaffected (it is read off the case). Persisting the
    artifacts is its own change, scoped with F12, and is deliberately NOT done by
    re-parsing suites.feature_text -- that would rebuild a DIFFERENT AC list from
    the one the suite was judged against. Never raises: a failure here must never
    break the core workbook."""
    try:
        artifacts = getattr(suite, "_rtm_artifacts", None)
        rows = (artifacts or {}).get("rows") or []
        if not rows:
            return
        # rtm_rows' output is rendered as-is.
        header_fmt, cell_fmt = _sheet_formats(workbook, "#1F4E79")
        ws = workbook.add_worksheet("Requirements Traceability")
        ws.set_column(0, 0, 14)
        ws.set_column(1, 1, 72)
        ws.set_column(2, 2, 8)
        ws.set_column(3, 3, 42)
        ws.set_column(4, 4, 14)
        _write_grid(ws, rows, header_fmt, cell_fmt)
    except Exception:
        logger.warning(
            "Failed writing the Requirements Traceability sheet - skipping it",
            exc_info=True,
        )


def _write_generation_notes_sheet(
    workbook: xlsxwriter.Workbook, suite: TestSuite
) -> None:
    """Write the "Generation Notes" sheet: what this run could not do.

    2026-08-15 (F5/F6/F7). Under-generated categories, an absent requirements
    checklist and a preflight that left no evidence it ran were all disclosed in
    the tool reply and all still reached finalized, exported suites -- because
    the reply is transient chat a summarising host model prunes, while this
    workbook is what the tester keeps and attaches to a ticket. The caveat has
    to travel with the deliverable.

    Deliberately the LAST sheet: it must not displace the test cases as the
    thing that opens first, and its absence is the normal, healthy case.

    No-op when the suite carries no notes, so a clean run's workbook is
    byte-identical. Never raises -- a failure here must never break the core
    workbook.
    """
    rows = getattr(suite, "_generation_notes", None)
    if not rows:
        return
    try:
        header_fmt, cell_fmt = _sheet_formats(workbook, "#9C2C2C")
        ws = workbook.add_worksheet("Generation Notes")
        ws.set_column(0, 0, 30)
        ws.set_column(1, 1, 96)
        ws.write(0, 0, "Finding", header_fmt)
        ws.write(0, 1, "Detail", header_fmt)
        for r, row in enumerate(rows, start=1):
            try:
                finding, detail = row[0], row[1]
            except (TypeError, IndexError, KeyError):
                finding, detail = "Note", row
            ws.write(r, 0, sanitize_cell(str(finding)), cell_fmt)
            ws.write(r, 1, sanitize_cell(str(detail)), cell_fmt)
    except Exception:
        logger.warning(
            "Failed writing the Generation Notes sheet - skipping it",
            exc_info=True,
        )


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
        header_fmt, cell_fmt = _sheet_formats(workbook, "#8B5E00")
        ws = workbook.add_worksheet("Assumed Requirements")
        ws.set_column(0, 1, 14)
        ws.set_column(2, max(2, len(rows[0]) - 1), 42)
        _write_grid(ws, rows, header_fmt, cell_fmt)
    except Exception:
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


class _CaseFormats(NamedTuple):
    """Every cell format the 'Test Cases' sheet writes with."""

    header: xlsxwriter.format.Format
    even: xlsxwriter.format.Format
    odd: xlsxwriter.format.Format
    even_rtl: xlsxwriter.format.Format
    odd_rtl: xlsxwriter.format.Format
    status_even: xlsxwriter.format.Format
    status_odd: xlsxwriter.format.Format
    priority_fill: dict


def _priority_fill_formats(workbook: xlsxwriter.Workbook) -> dict:
    # Direct (baked-in) fills for the Priority column. Apple Numbers drops Excel
    # *conditional* formatting on import, so the value-based colors of the
    # conditional rules would vanish there. Writing the color straight onto the
    # cell makes it show in Numbers too. The conditional_format rules are kept,
    # so Excel / LibreOffice / Google Sheets still re-color live when a Priority
    # is changed (a conditional format overrides the direct fill in those apps).
    fills = {}
    for name, bg, font, bold in (
        ("Critical", "#FF4444", "#FFFFFF", True),
        ("High", "#FFC7CE", "#9C0006", False),
        ("Medium", "#FFEB9C", "#9C6500", False),
        ("Low", "#C6EFCE", "#006100", False),
    ):
        props = {"bg_color": bg, "font_color": font, "border": 1}
        if bold:
            props["bold"] = True
        fills[name] = workbook.add_format({**props, "valign": "top", "text_wrap": True})
    return fills


def _case_cell_format(
    workbook: xlsxwriter.Workbook, bg: str, rtl: bool = False
) -> xlsxwriter.format.Format:
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


def _case_header_format(workbook: xlsxwriter.Workbook) -> xlsxwriter.format.Format:
    return workbook.add_format(
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


def _status_base_format(workbook: xlsxwriter.Workbook) -> xlsxwriter.format.Format:
    # Status column needs its own base formats: centered + no bg so conditional format colors show
    return workbook.add_format(
        {
            "bg_color": "#D9D9D9",
            "font_color": "#595959",
            "border": 1,
            "align": "center",
            "valign": "vcenter",
        }
    )


def _build_case_formats(workbook: xlsxwriter.Workbook) -> _CaseFormats:
    header_fmt = _case_header_format(workbook)
    even_fmt = _case_cell_format(workbook, "#FFFFFF")
    odd_fmt = _case_cell_format(workbook, "#EBF3FB")
    even_rtl_fmt = _case_cell_format(workbook, "#FFFFFF", rtl=True)
    odd_rtl_fmt = _case_cell_format(workbook, "#EBF3FB", rtl=True)
    _pri_fill = _priority_fill_formats(workbook)
    status_odd_fmt = _status_base_format(workbook)
    status_even_fmt = _status_base_format(workbook)
    return _CaseFormats(
        header=header_fmt,
        even=even_fmt,
        odd=odd_fmt,
        even_rtl=even_rtl_fmt,
        odd_rtl=odd_rtl_fmt,
        status_even=status_even_fmt,
        status_odd=status_odd_fmt,
        priority_fill=_pri_fill,
    )


def _tc_id_key(tc: object) -> int:
    """Sort key: the digits of the TC-ID (0 when it has none)."""
    digits = "".join(ch for ch in (getattr(tc, "tc_id", "") or "") if ch.isdigit())
    return int(digits) if digits else 0


def _write_cases_sheet(workbook: xlsxwriter.Workbook, suite: TestSuite) -> int:
    """Write the 'Test Cases' sheet; returns the last data row (1-based)."""
    fmts = _build_case_formats(workbook)
    ws = workbook.add_worksheet("Test Cases")

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
    sorted_cases = sorted(suite.test_cases, key=_tc_id_key)

    # Notes for the Notes column: the Batch 3 standing-rules pack attaches
    # a mechanical assumption / clarification label per tc_id. Absent =>
    # every Notes cell stays empty, exactly as before.
    rule_pack_notes = getattr(suite, "_rule_pack_notes", None) or {}

    for row_idx, tc in enumerate(sorted_cases, start=1):
        _write_case_row(ws, row_idx, tc, fmts, rule_pack_notes.get(tc.tc_id, ""))

    last_data_row = len(suite.test_cases) + 1
    _add_priority_rules(workbook, ws, last_data_row)
    _add_status_rules(workbook, ws, last_data_row)
    return last_data_row


def _write_case_row(
    ws: object,
    row_idx: int,
    tc: object,
    fmts: _CaseFormats,
    rule_pack_note: str,
) -> None:
    """Write one test case as row *row_idx* and size the row to its content."""
    odd = row_idx % 2 == 1
    fmt = fmts.odd if odd else fmts.even
    status_fmt = fmts.status_odd if odd else fmts.status_even
    cell = _RowWriter(ws, row_idx, fmt, fmts.odd_rtl if odd else fmts.even_rtl)
    texts = _case_texts(tc, rule_pack_note)

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
    cell.prepared(_COL_STEPS, texts.steps)
    cell.prepared(_COL_TESTDATA, texts.test_data)
    cell.prepared(_COL_EXPECTED, texts.expected)
    ws.write(row_idx, _COL_STATUS, "Not Run", status_fmt)
    cell.text(_COL_NOTES, texts.notes)
    # F6: which of the 8 generation categories produced this case. Empty when
    # it could not be resolved -- never guessed. A value self-reported by the
    # host model is normalised before it reaches here.
    cell.text(_COL_CATEGORY, getattr(tc, "category", None) or "")
    # F06: the acceptance criterion this case verifies -- the evidence for
    # the reply's "N/N traced" claim, in the file the tester keeps.
    cell.text(_COL_REQUIREMENT, texts.requirement)
    cell.risk_score(_COL_RISK_SCORE, _risk_score_cell(tc))
    ws.set_row(row_idx, _row_height_for(_row_cells(tc, texts)))


class _RowWriter:
    """Writes the cells of one 'Test Cases' row with its alternating format."""

    def __init__(self, ws: object, row_idx: int, fmt: object, rtl_fmt: object):
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


def _case_texts(tc: object, rule_pack_note: str) -> _CaseTexts:
    # Combine steps into a single multi-line string: "1. action\n2. action"
    # Wrapped in sanitize_cell() -- this text originates from LLM-generated or
    # Jira-derived content and must not be interpreted as a spreadsheet formula.
    steps_text = _prepare("\n".join(f"{s.step_number}. {s.action}" for s in tc.steps))

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
    return _CaseTexts(
        steps=steps_text,
        expected=expected_text,
        test_data=_prepare("\n".join(data_lines)),
        notes=_notes_cell(tc, rule_pack_note),
        requirement=_requirement_cell(tc),
    )


def _row_cells(tc: object, texts: _CaseTexts) -> list:
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


def _add_priority_rules(
    workbook: xlsxwriter.Workbook, ws: object, last_data_row: int
) -> None:
    """Conditional format on the Priority column, letter DERIVED from the index."""
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
    _pri = _col_letter(_COL_PRIORITY)
    pri_range = f"{_pri}2:{_pri}{last_data_row}"
    for value, fmt in (
        ("Critical", fmt_critical),
        ("High", fmt_high),
        ("Medium", fmt_medium),
        ("Low", fmt_low),
    ):
        ws.conditional_format(
            pri_range,
            {
                "type": "cell",
                "criteria": "equal to",
                "value": f'"{value}"',
                "format": fmt,
            },
        )


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


def _add_status_rules(
    workbook: xlsxwriter.Workbook, ws: object, last_data_row: int
) -> None:
    """Status column: colour-coded conditional formats plus the dropdown."""
    _stat = _col_letter(_COL_STATUS)
    status_col_range = f"{_stat}2:{_stat}{last_data_row}"
    for value, fmt in _status_rule_formats(workbook):
        ws.conditional_format(
            status_col_range,
            {
                "type": "cell",
                "criteria": "equal to",
                "value": f'"{value}"',
                "format": fmt,
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


def _write_workbook(workbook: xlsxwriter.Workbook, suite: TestSuite) -> None:
    last_data_row = _write_cases_sheet(workbook, suite)
    _write_summary_sheet(workbook, suite, last_data_row)


def _write_summary_sheet(
    workbook: xlsxwriter.Workbook, suite: TestSuite, last_data_row: int
) -> None:
    """The 'Summary' sheet: COUNTIF formulas over the 'Test Cases' columns."""
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

    _write_priority_block(summary_ws, suite, last_data_row, label_fmt, value_fmt)
    _write_type_block(summary_ws, suite, last_data_row, label_fmt, value_fmt)


def _write_priority_block(
    summary_ws: object,
    suite: TestSuite,
    last_data_row: int,
    label_fmt: xlsxwriter.format.Format,
    value_fmt: xlsxwriter.format.Format,
) -> None:
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


def _write_type_block(
    summary_ws: object,
    suite: TestSuite,
    last_data_row: int,
    label_fmt: xlsxwriter.format.Format,
    value_fmt: xlsxwriter.format.Format,
) -> None:
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
