"""Convert a TestSuite to a TestRail-compatible CSV file for import into TestRail/Xray/Zephyr."""

from __future__ import annotations

import csv
import logging
import tempfile
import time
from pathlib import Path

from tools.cell_sanitizer import sanitize_cell
from tools.models import TestSuite, format_test_data_lines
from tools.secure_temp import SUBDIR_NAME, make_secure_temp_path

logger = logging.getLogger(__name__)

# Standard TestRail CSV import columns. References / Custom Risk Score /
# Stable ID are intentionally NOT exported here — see the matching comment in
# xlsx_generator.py (those fields remain on the TestCase model for internal
# use, e.g. tools/testrail_pusher.py still pushes stable_id as the API refs field).
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
    if output_path is None:
        output_path = make_secure_temp_path(
            prefix="qa_test_cases_", suffix="_testrail.csv"
        )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # The optional "Test Data" column is added ONLY when at least one case carries a
    # data-provisioning plan (QA_TEST_DATA_STRATEGY). With none, both the header and
    # every row are byte-identical to the pre-feature export.
    has_test_data = any(tc.test_data for tc in suite.test_cases)
    headers = _HEADERS + ["Test Data"] if has_test_data else _HEADERS

    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        for tc in suite.test_cases:
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
                row.append(
                    sanitize_cell("\n".join(format_test_data_lines(tc.test_data)))
                )
            writer.writerow(row)

    logger.info(
        "TestRail CSV written: %s (%d test cases)", output_path, len(suite.test_cases)
    )
    return output_path


def cleanup_temp_files(max_age_seconds: int = 3600) -> int:
    """Delete stale TestRail-CSV exports older than max_age_seconds. Returns count.

    NB-020: sweep ONLY the app's own secure per-app subdir — never the shared
    /tmp root, where a same-named file could belong to an unrelated user. The glob
    (``*_testrail.csv``) is specific to this exporter's output.
    """
    base = Path(tempfile.gettempdir()) / SUBDIR_NAME
    if not base.is_dir():
        return 0
    now = time.time()
    deleted = 0
    for path in base.glob("qa_test_cases_*_testrail.csv"):
        try:
            age = now - path.stat().st_mtime
            if age > max_age_seconds:
                path.unlink(missing_ok=True)
                deleted += 1
                logger.info(
                    "Cleaned up stale TestRail CSV temp file: %s (age %.0fs)",
                    path,
                    age,
                )
        except OSError:
            logger.warning("Could not check/delete temp file: %s", path)
    if deleted:
        logger.info("TestRail CSV cleanup: removed %d stale file(s)", deleted)
    return deleted
