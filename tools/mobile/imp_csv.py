"""A ``.csv`` path or its text -> cases."""

from __future__ import annotations

import csv
import io
import logging

from tools.mobile.imp_read import _read_text
from tools.mobile.imp_table import _assemble
from tools.mobile.imp_text import _clean, fail

logger = logging.getLogger("tools.mobile.importers")

_NO_TABLE = (
    "That CSV has no header row plus at least one case row, so nothing was imported."
)


def _csv_rows(body: str) -> list[list[str]]:
    reader = csv.reader(io.StringIO(body))
    rows = [[_clean(cell) for cell in row] for row in reader]
    return [row for row in rows if any(row)]


def from_csv(source: object) -> dict:
    """A ``.csv`` path or its text."""
    try:
        text = _read_text(source)
        if text.get("error"):
            return text
        rows = _csv_rows(str(text["content"]))
        if len(rows) < 2:
            return fail(_NO_TABLE)
        return _assemble(rows, 2)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.importers.from_csv failed")
        return fail(str(exc))
