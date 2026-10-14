"""An ``.xlsx`` path -> cases, read with ``zipfile`` and hardened."""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

from tools.mobile.imp_consts import MAX_BYTES, MAX_UNCOMPRESSED_BYTES, MAX_ZIP_MEMBERS
from tools.mobile.imp_read import _too_big
from tools.mobile.imp_sheet import _shared_strings, _sheet_rows, _sheet_xml
from tools.mobile.imp_source import vet_source_path
from tools.mobile.imp_table import _assemble
from tools.mobile.imp_text import fail

logger = logging.getLogger("tools.mobile.importers")

_NO_TABLE = (
    "That workbook's first sheet has no header row plus at "
    "least one case row, so nothing was imported."
)


def _size_problem(infos: list[zipfile.ZipInfo]) -> dict | None:
    if len(infos) > MAX_ZIP_MEMBERS:
        return fail(
            "That workbook contains "
            + str(len(infos))
            + " internal files, more than the "
            + str(MAX_ZIP_MEMBERS)
            + " allowed, and was not opened."
        )
    declared = sum(int(info.file_size or 0) for info in infos)
    if declared > MAX_UNCOMPRESSED_BYTES:
        return fail(
            "That workbook expands to "
            + str(declared)
            + " bytes, more than the "
            + str(MAX_UNCOMPRESSED_BYTES)
            + " allowed, and was not opened."
        )
    return None


def _archive_rows(archive: zipfile.ZipFile) -> dict:
    """Size-check the workbook, then read its first sheet's rows."""
    problem = _size_problem(archive.infolist())
    if problem:
        return problem
    shared = _shared_strings(archive)
    if shared.get("error"):
        return shared
    sheet = _sheet_xml(archive)
    if sheet.get("error"):
        return sheet
    rows = _sheet_rows(str(sheet["content"]), list(shared["content"]))
    if rows.get("error"):
        return rows
    return {"error": None, "content": list(rows["content"])}


def _workbook_rows(path: Path) -> dict:
    if not path.is_file():
        return fail("There is no file at " + str(path) + ".")
    vet_source_path(path)
    if path.stat().st_size > MAX_BYTES:
        return fail(_too_big(path.name))
    with zipfile.ZipFile(path) as archive:
        return _archive_rows(archive)


# ``openpyxl`` is not a dependency and ``xlsxwriter`` writes only, so the
# read side is ours. The hardening is not optional: the file arrived from
# outside.
def from_xlsx(source: object) -> dict:
    """An ``.xlsx`` read with ``zipfile`` + ``xml.etree``, hardened."""
    try:
        rows = _workbook_rows(Path(str(source or "")).expanduser())
        if rows.get("error"):
            return rows
        table = [row for row in rows["content"] if any(cell for cell in row)]
        if len(table) < 2:
            return fail(_NO_TABLE)
        return _assemble(table, 2)
    except zipfile.BadZipFile:
        return fail("That file is not a readable .xlsx workbook.")
    except OSError as exc:  # includes the PermissionError from vet_source_path
        return fail("Could not read that workbook: " + str(exc))
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.importers.from_xlsx failed")
        return fail(str(exc))
