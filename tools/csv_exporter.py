"""Convert a TestSuite to a generic CSV file using the same column layout as xlsx_generator.py."""

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

# Requirement ID / Risk Score / Risk Label / Risk Rationale / Stable ID are
# intentionally NOT exported here — see the matching comment in xlsx_generator.py.
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


def generate_test_case_csv(suite: TestSuite, output_path: str | None = None) -> str:
    """Write suite to a CSV file and return the file path.

    Raises OSError or ValueError on genuine file I/O errors.
    """
    if output_path is None:
        output_path = make_secure_temp_path(prefix="qa_test_cases_", suffix=".csv")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(_HEADERS)
        for tc in suite.test_cases:
            steps_text = sanitize_cell(
                "\n".join(f"{s.step_number}. {s.action}" for s in tc.steps)
            )
            expected_text = sanitize_cell(
                "\n".join(f"{s.step_number}. {s.expected_result}" for s in tc.steps)
            )
            data_lines = [
                f"Step {s.step_number}: {s.test_data}" for s in tc.steps if s.test_data
            ]
            # Case-level data-provisioning plan (QA_TEST_DATA_STRATEGY): appended
            # after the per-step lines; a case with none is byte-identical to before.
            data_lines.extend(format_test_data_lines(tc.test_data))
            test_data_text = sanitize_cell("\n".join(data_lines))
            writer.writerow(
                [
                    tc.tc_id,
                    sanitize_cell(tc.module),
                    sanitize_cell(tc.title),
                    tc.priority.value,
                    tc.type.value,
                    sanitize_cell(tc.preconditions or ""),
                    steps_text,
                    test_data_text,
                    expected_text,
                    "Not Run",
                    "",
                ]
            )

    logger.info("CSV written: %s (%d test cases)", output_path, len(suite.test_cases))
    return output_path


def cleanup_temp_files(max_age_seconds: int = 3600) -> int:
    """Delete stale generic-CSV exports older than max_age_seconds. Returns count.

    NB-020: sweep ONLY the app's own secure per-app subdir — never the shared
    /tmp root, where a same-named file could belong to an unrelated user. The glob
    is also made specific so it does not double-sweep the TestRail exports
    (``*_testrail.csv``), which testrail_exporter owns.
    """
    base = Path(tempfile.gettempdir()) / SUBDIR_NAME
    if not base.is_dir():
        return 0
    now = time.time()
    deleted = 0
    for path in base.glob("qa_test_cases_*.csv"):
        # Skip the TestRail variant — it is cleaned by testrail_exporter.
        if path.name.endswith("_testrail.csv"):
            continue
        try:
            age = now - path.stat().st_mtime
            if age > max_age_seconds:
                path.unlink(missing_ok=True)
                deleted += 1
                logger.info("Cleaned up stale CSV temp file: %s (age %.0fs)", path, age)
        except OSError:
            logger.warning("Could not check/delete temp file: %s", path)
    if deleted:
        logger.info("CSV cleanup: removed %d stale file(s)", deleted)
    return deleted
