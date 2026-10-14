"""Cell-level helpers (text preparation, row height, notes/risk/requirement cells)."""

from __future__ import annotations

import logging
import math
from typing import Any

from tools.bilingual import bidi_isolate
from tools.cell_sanitizer import sanitize_cell
from tools.models import display_requirement_id
from tools.xlsx_cols import _CELL_ERRORS, _UNTRACED_LABEL

logger = logging.getLogger("tools.xlsx_generator")


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
    except _CELL_ERRORS:  # pragma: no cover - defensive
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


def _notes_cell(tc: Any, rule_pack_note: str) -> str:
    """Text for the Notes column of one case.

    v1.97.0 cursor-hardening (item 12): the risk score used to be glued into
    this cell as "Risk <score>" text (2026-08-04 - 2026-09-26), making it
    unsortable and unfilterable and duplicating nothing else on the row. The
    score now has its own column (_risk_score_cell / _COL_RISK_SCORE below);
    this cell carries ONLY the rule-pack note, exactly the pre-2026-08-04
    output. Pure and never raises.
    """
    return rule_pack_note or ""


def _risk_score_cell(tc: Any) -> int | None:
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
    except _CELL_ERRORS:  # pragma: no cover - defensive
        logger.debug("risk score cell rendering failed -- left blank", exc_info=True)
        return None


def _requirement_cell(tc: Any) -> str:
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
    except _CELL_ERRORS:  # pragma: no cover - defensive
        logger.debug("requirement cell rendering failed", exc_info=True)
        return _UNTRACED_LABEL
