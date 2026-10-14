"""Writing the XLSX, generic CSV and TestRail CSV for a finalized suite."""

from __future__ import annotations

import asyncio
import logging

from agents.scenario_report import (
    _CSV_WARNING,
    _TESTRAIL_WARNING,
    _XLSX_FILE_NOTE,
    _XLSX_WARNING,
)
from tools.csv_exporter import generate_test_case_csv
from tools.testrail_exporter import generate_testrail_csv
from tools.xlsx_generator import generate_test_case_xlsx

logger = logging.getLogger("agents.test_scenario_agent")


async def _run_exporter(suite, spec) -> tuple[str, str]:
    """Run one file exporter off the event loop: (path, "") on success,
    ("", warning) when it raises. ``spec`` is (generator, failure_log, warning)."""
    generator, failure_log, warning = spec
    try:
        return await asyncio.to_thread(generator, suite), ""
    except Exception:
        logger.exception(failure_log)
        return "", warning


def _export_lines(csv_path: str, testrail_path: str) -> str:
    """The "Export Files" block naming the CSV paths ("" when neither exists)."""
    if not (csv_path or testrail_path):
        return ""
    export_lines = ["\n\n## Export Files"]
    if csv_path:
        export_lines.append(f"- CSV (generic): `{csv_path}`")
    if testrail_path:
        export_lines.append(f"- TestRail CSV: `{testrail_path}`")
    return "\n".join(export_lines)


async def _export_files(suite) -> tuple[str, str, str, str, str]:
    """Write the XLSX, generic CSV and TestRail CSV for ``suite``. Returns
    (xlsx_path, csv_path, testrail_path, file_note, export_section). The
    generators are looked up at call time, so a patched module attribute is
    the one that runs."""
    xlsx_path, xlsx_warning = await _run_exporter(
        suite, (generate_test_case_xlsx, "XLSX generation failed", _XLSX_WARNING)
    )
    csv_path, csv_warning = await _run_exporter(
        suite, (generate_test_case_csv, "CSV generation failed", _CSV_WARNING)
    )
    testrail_path, testrail_warning = await _run_exporter(
        suite,
        (generate_testrail_csv, "TestRail CSV generation failed", _TESTRAIL_WARNING),
    )
    file_note = _XLSX_FILE_NOTE if xlsx_path else xlsx_warning
    export_section = _export_lines(csv_path, testrail_path)
    export_section += csv_warning + testrail_warning
    return xlsx_path, csv_path, testrail_path, file_note, export_section
