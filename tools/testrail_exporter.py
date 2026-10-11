"""Convert a TestSuite to a TestRail-compatible CSV file for import into TestRail/Xray/Zephyr."""

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
    cleanup_temp_files as _sweep_temp_files,
)

logger = logging.getLogger(__name__)

# Standard TestRail CSV import columns. Custom Risk Score / Stable ID are
# intentionally NOT exported here — see the matching comment in
# xlsx_generator.py (those fields remain on the TestCase model for internal
# use, e.g. tools/testrail_pusher.py still pushes stable_id as the API refs field).
# `References` is OPTIONAL and appended below when at least one case carries a
# requirement tag (F06, 2026-08-19) — this CSV is imported into TestRail / Xray /
# Zephyr, where a requirement link is a first-class field.
_HEADERS = [
    "Title",
    "Section",
    "Type",
    "Priority",
    "Estimate",
    "Steps",
    "Expected Result",
]


def generate_testrail_csv(suite: TestSuite, output_path: str | None = None) -> str:
    """Write suite to a TestRail-format CSV file and return the file path.

    Raises OSError or ValueError on genuine file I/O errors.
    """
    output_path = resolve_output_path(output_path, "_testrail.csv")

    # The optional "Test Data" column is added ONLY when at least one case carries a
    # data-provisioning plan. With none, both the header and every row are
    # byte-identical to the pre-feature export.
    has_test_data = any(tc.test_data for tc in suite.test_cases)
    # F06: TestRail's own requirement-link column, conditional for exactly the
    # reason "Test Data" is -- with no tagged case the header and every row stay
    # byte-identical to the pre-feature export.
    #
    # DELIBERATE DIVERGENCE from tools/testrail_pusher.py, which sends
    # `stable_id` in the API `refs` field: a CSV a human imports wants the
    # acceptance criterion, and changing what the live credentialed pusher sends
    # is a decision of its own, not a side effect of adding a column here.
    has_refs = any((tc.requirement_id or "").strip() for tc in suite.test_cases)
    headers = list(_HEADERS)
    if has_test_data:
        headers.append("Test Data")
    if has_refs:
        headers.append("References")

    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        for tc in suite.test_cases:
            writer.writerow(_generate_testrail_csv_row(tc, has_test_data, has_refs))

    logger.info(
        "TestRail CSV written: %s (%d test cases)", output_path, len(suite.test_cases)
    )
    return output_path


def _generate_testrail_csv_row(tc, has_test_data: bool, has_refs: bool) -> list:
    """One CSV row for ``tc``, with the optional columns the header carries."""
    steps_text = sanitize_cell(
        "\n".join(f"{s.step_number}. {s.action}" for s in tc.steps)
    )
    expected_text = sanitize_cell(
        "\n".join(f"{s.step_number}. {s.expected_result}" for s in tc.steps)
    )
    row = [
        sanitize_cell(tc.title),
        sanitize_cell(tc.module),
        tc.type.value,
        tc.priority.value,
        "",
        steps_text,
        expected_text,
    ]
    if has_test_data:
        row.append(sanitize_cell("\n".join(format_test_data_lines(tc.test_data))))
    if has_refs:
        # BLANK for an untraced case, not the "(untraced)" label the
        # xlsx/csv columns use: this file is machine-imported, and a TMS
        # would ingest that literal as a reference string.
        row.append(sanitize_cell(display_requirement_id(tc.requirement_id)))
    return row


def cleanup_temp_files(max_age_seconds: int = 3600) -> int:
    """Delete stale TestRail-CSV exports older than max_age_seconds. Returns count.

    NB-020: sweep ONLY the app's own secure per-app subdir — never the shared
    /tmp root, where a same-named file could belong to an unrelated user. The glob
    (``*_testrail.csv``) is specific to this exporter's output.
    """
    return _sweep_temp_files(
        max_age_seconds=max_age_seconds,
        pattern="qa_test_cases_*_testrail.csv",
        label="TestRail CSV",
        log=logger,
    )
