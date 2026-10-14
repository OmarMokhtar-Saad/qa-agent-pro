"""A pasted markdown (or pipe-delimited) table -> cases."""

from __future__ import annotations

import logging
import re

from tools.mobile.imp_consts import MAX_BYTES
from tools.mobile.imp_table import _assemble
from tools.mobile.imp_text import _clean_document, fail

logger = logging.getLogger("tools.mobile.importers")

_NO_TABLE = (
    "That paste does not contain a table with a header row and "
    "at least one case row, so nothing was imported."
)


def _paste_body(text: object) -> tuple[str, dict | None]:
    """The normalised paste, or an error dict (byte cap, then emptiness)."""
    # The byte cap is checked on the RAW text, BEFORE any normalisation:
    # a cap applied after a TRUNCATING cleaner is not a cap at all, which
    # is exactly how 199 rows of a 299-row paste went missing in silence.
    raw = text if isinstance(text, str) else ("" if text is None else str(text))
    if len(raw.encode("utf-8", errors="replace")) > MAX_BYTES:
        return "", fail(
            "That paste is larger than the "
            + str(MAX_BYTES)
            + " byte cap and was not imported."
        )
    # Document-level normalisation, which never trims content.
    body = _clean_document(text)
    if not body:
        return "", fail("Nothing was pasted.")
    return body, None


def _markdown_rows(body: str) -> tuple[list[list[str]], int]:
    rows: list[list[str]] = []
    first_data_row = 0
    for number, line in enumerate(body.split("\n"), start=1):
        stripped = line.strip()
        if not stripped or "|" not in stripped:
            continue
        if re.fullmatch(r"\|?[\s:|-]+\|?", stripped):
            continue  # the ---|--- separator
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        # A markdown cell escapes a literal newline as <br>.
        cells = [re.sub(r"(?i)<br\s*/?>", "\n", cell) for cell in cells]
        rows.append(cells)
        if len(rows) == 2:
            first_data_row = number
    return rows, first_data_row


def from_markdown(text: object) -> dict:
    """A pasted markdown (or plain pipe-delimited) table."""
    try:
        body, error = _paste_body(text)
        if error is not None:
            return error
        rows, first_data_row = _markdown_rows(body)
        if len(rows) < 2:
            return fail(_NO_TABLE)
        return _assemble(rows, first_data_row or 2)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.importers.from_markdown failed")
        return fail(str(exc))
