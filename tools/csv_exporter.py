"""Convert a TestSuite to a generic CSV file using the same column layout as xlsx_generator.py."""

from __future__ import annotations

import csv
import logging
import tempfile  # noqa: F401  # tests patch tools.<module>.tempfile.gettempdir

from tools.cell_sanitizer import sanitize_cell
from tools.export_common import resolve_output_path
from tools.models import (
    TestSuite,
    display_requirement_id,
    format_test_data_lines,
)
from tools.secure_temp import (
    Sweep,
)
from tools.secure_temp import (
    cleanup_temp_files as _sweep_temp_files,
)
from tools.xlsx_generator import _risk_score_cell

logger = logging.getLogger(__name__)

# Risk Label / Risk Rationale / Stable ID are intentionally NOT exported here —
# see the matching comment in xlsx_generator.py. Requirement ID left that list on
# 2026-08-19 (F06); Risk Score left it in v1.97.0 (item 12) and is the 14th
# column, in lockstep with the xlsx as always — the cell itself comes from
# xlsx_generator._risk_score_cell so the two cannot drift.
# Kept in lockstep with xlsx_generator._HEADERS -- an invariant asserted by
# tests/test_csv_exporter.test_csv_headers_match_xlsx_headers. "Test Type" /
# "Coverage Category" renamed 2026-08-04 with the xlsx; nothing external reads
# these names (TestRail and Zephyr each carry their own header set).
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


def generate_test_case_csv(suite: TestSuite, output_path: str | None = None) -> str:
    """Write suite to a CSV file and return the file path.

    Raises OSError or ValueError on genuine file I/O errors.
    """
    output_path = resolve_output_path(output_path, ".csv")

    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(_HEADERS)
        for tc in suite.test_cases:
            writer.writerow(_case_row(tc))

    logger.info("CSV written: %s (%d test cases)", output_path, len(suite.test_cases))
    return output_path


def _test_data_text(tc) -> str:
    """The Test Data cell: per-step lines, then the case-level plan."""
    data_lines = [
        f"Step {s.step_number}: {s.test_data}" for s in tc.steps if s.test_data
    ]
    # Case-level data-provisioning plan: appended after the per-step
    # lines; a case with none is byte-identical to before.
    data_lines.extend(format_test_data_lines(tc.test_data))
    return sanitize_cell("\n".join(data_lines))


def _step_text(tc, attr: str) -> str:
    """A numbered per-step column (action or expected result)."""
    return sanitize_cell(
        "\n".join(f"{s.step_number}. {getattr(s, attr)}" for s in tc.steps)
    )


def _ident_cells(tc) -> list:
    """The identity columns: id, module, title, priority, type."""
    return [
        tc.tc_id,
        sanitize_cell(tc.module),
        sanitize_cell(tc.title),
        tc.priority.value,
        tc.type.value,
    ]


def _body_cells(tc) -> list:
    """The body columns: preconditions, steps, test data, expected, status."""
    return [
        sanitize_cell(tc.preconditions or ""),
        _step_text(tc, "action"),
        _test_data_text(tc),
        _step_text(tc, "expected_result"),
        "Not Run",
        "",
    ]


# sanitize_cell is defence-in-depth, not a necessity: the
# value is normalised to one of 8 canonical names before it
# gets here, but TestCase.category itself accepts any
# <=60-char string, so a future path could bypass that.
# F06: the acceptance criterion this case verifies.
# "(untraced)" rather than a blank, for the reason
# xlsx_generator._requirement_cell gives.
# An unscored suite writes an EMPTY cell, not "0" -- the
# xlsx writes write_blank for the same reason.
def _trace_cells(tc) -> list:
    """The trailing columns: category, requirement, risk score."""
    risk = _risk_score_cell(tc)
    return [
        sanitize_cell(getattr(tc, "category", None) or ""),
        sanitize_cell(display_requirement_id(tc.requirement_id) or "(untraced)"),
        risk if risk is not None else "",
    ]


def _case_row(tc) -> list:
    """One CSV row for a test case, in _HEADERS order."""
    return [*_ident_cells(tc), *_body_cells(tc), *_trace_cells(tc)]


def cleanup_temp_files(max_age_seconds: int = 3600) -> int:
    """Delete stale generic-CSV exports older than max_age_seconds. Returns count.

    NB-020: sweep ONLY the app's own secure per-app subdir — never the shared
    /tmp root, where a same-named file could belong to an unrelated user. The glob
    is also made specific so it does not double-sweep the TestRail exports
    (``*_testrail.csv``), which testrail_exporter owns.
    """
    return _sweep_temp_files(
        Sweep(
            max_age_seconds=max_age_seconds,
            pattern="qa_test_cases_*.csv",
            label="CSV",
            skip_suffix="_testrail.csv",
            log=logger,
        )
    )
