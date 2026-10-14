"""Table-row cell parsing for the bilingual rule pack."""

from __future__ import annotations

import re

from tools import bil_key
from tools.bil_key import _KEY_RE
from tools.bil_script import _AR_CHAR_RE, _LATIN_RE

_SEP_CELL_RE = re.compile(r"^[\s:.\-=_|]*$")


def _cells(line: str) -> list[str]:
    """Split a markdown-pipe or tab-delimited table row into cells."""
    if "|" in line:
        return [c.strip() for c in line.strip().strip("|").split("|")]
    if "\t" in line:
        return [c.strip() for c in line.split("\t")]
    return []


def _pair_from_cells(cells: list[str]) -> tuple[str, str, str] | None:
    """(key, en, ar) from a table row, or None when the row is not a pair row."""
    if len(cells) < 2 or all(_SEP_CELL_RE.match(c) for c in cells):
        return None
    key, key_idx = _find_key_cell(cells)
    ar_idx = next(
        (i for i, c in enumerate(cells) if i != key_idx and _AR_CHAR_RE.search(c)),
        -1,
    )
    if ar_idx < 0:
        return None
    en_idx = _find_en_cell(cells, (key_idx, ar_idx))
    if en_idx < 0:
        return None
    return key, bil_key._clean_value(cells[en_idx]), bil_key._clean_value(cells[ar_idx])


def _find_key_cell(cells: list[str]) -> tuple[str, int]:
    """(normalized key, cell index) of the first cell holding a key; ("", -1) if none."""
    for i, cell in enumerate(cells):
        m = _KEY_RE.search(cell)
        if m:
            return bil_key.normalize_key(m.group(1)), i
    return "", -1


def _find_en_cell(cells: list[str], skip: tuple[int, int]) -> int:
    """Index of the cell with the most Latin letters outside ``skip``; -1 if none."""
    en_idx, best = -1, 0
    for i, cell in enumerate(cells):
        if i in skip:
            continue
        n = len(_LATIN_RE.findall(cell))
        if n > best:
            best, en_idx = n, i
    return en_idx
