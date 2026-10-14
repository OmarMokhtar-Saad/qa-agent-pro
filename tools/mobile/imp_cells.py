"""Cell-level reads of one worksheet row."""

from __future__ import annotations

from xml.etree import ElementTree

from tools.mobile.imp_consts import _CELL_REF_RE
from tools.mobile.imp_text import _clean


def _column_index(ref: str) -> int:
    match = _CELL_REF_RE.match(str(ref or "").upper())
    if not match:
        return -1
    letters = match.group(1)
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - 64)
    return index - 1


def _row_index(ref: str) -> int:
    match = _CELL_REF_RE.match(str(ref or "").upper())
    return int(match.group(2)) if match else -1


def _strip_ns(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _cell_value(cell: ElementTree.Element, shared: list[str]) -> str:
    kind = str(cell.get("t") or "")
    value = ""
    for child in cell:
        name = _strip_ns(child.tag)
        if name == "v":
            value = child.text or ""
        elif name == "is":
            value = "".join(
                node.text or "" for node in child.iter() if _strip_ns(node.tag) == "t"
            )
    if kind == "s":
        try:
            value = shared[int(value)]
        except (ValueError, IndexError):
            value = ""
    return value


def _row_values(row: ElementTree.Element, shared: list[str]) -> dict[int, str]:
    cells: dict[int, str] = {}
    for cell in row:
        if _strip_ns(cell.tag) != "c":
            continue
        column = _column_index(cell.get("r") or "")
        if column < 0:
            column = len(cells)
        cells[column] = _clean(_cell_value(cell, shared))
    return cells
