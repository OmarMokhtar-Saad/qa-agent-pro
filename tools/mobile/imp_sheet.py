"""The shared strings and the rows of the workbook's sheet."""

from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree

from tools.mobile import imp_consts
from tools.mobile.imp_cells import _row_values, _strip_ns
from tools.mobile.imp_consts import SHEET_NAME
from tools.mobile.imp_member import _member_text
from tools.mobile.imp_text import _clean, fail

_FIRST_SHEET = "xl/worksheets/sheet1.xml"


def _si_texts(root: ElementTree.Element) -> list[str]:
    out: list[str] = []
    for item in root:
        if _strip_ns(item.tag) != "si":
            continue
        pieces = [node.text or "" for node in item.iter() if _strip_ns(node.tag) == "t"]
        out.append(_clean("".join(pieces)))
    return out


def _shared_strings(archive: zipfile.ZipFile) -> dict:
    read = _member_text(archive, "xl/sharedStrings.xml")
    if read.get("error"):
        return read
    text = str(read["content"] or "")
    if not text.strip():
        return {"error": None, "content": []}
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return fail("That workbook's string table is not valid XML.")
    return {"error": None, "content": _si_texts(root)}


def _named_sheet(text: str, names: set[str]) -> str:
    target = _FIRST_SHEET
    try:
        root = ElementTree.fromstring(text)
        order = [node for node in root.iter() if _strip_ns(node.tag) == "sheet"]
        for position, node in enumerate(order, start=1):
            if str(node.get("name") or "").strip() == SHEET_NAME:
                candidate = "xl/worksheets/sheet" + str(position) + ".xml"
                if candidate in names:
                    target = candidate
                break
    except ElementTree.ParseError:
        pass  # fall through to sheet1
    return target


def _sheet_xml(archive: zipfile.ZipFile) -> dict:
    """The ``Test Cases`` sheet if the workbook names one, else sheet1."""
    names = {info.filename for info in archive.infolist()}
    book = _member_text(archive, "xl/workbook.xml")
    if book.get("error"):
        return book
    text = str(book["content"] or "")
    target = _named_sheet(text, names) if text.strip() else _FIRST_SHEET
    if target not in names:
        return fail("That workbook has no readable first worksheet.")
    return _member_text(archive, target)


def _dense(cells: dict[int, str]) -> list[str]:
    if not cells:
        return []
    return [cells.get(index, "") for index in range(max(cells) + 1)]


def _sheet_rows(sheet_xml: str, shared: list[str]) -> dict:
    """Rows of the sheet, read as a stream and stopped past ``MAX_ROWS``."""
    rows: list[list[str]] = []
    try:
        stream = io.BytesIO(sheet_xml.encode("utf-8"))
        for _event, row in ElementTree.iterparse(stream, events=("end",)):
            if _strip_ns(row.tag) != "row":
                continue
            cells = _row_values(row, shared)
            row.clear()
            rows.append(_dense(cells))
            # blank rows never trigger the cap
            if cells and len(rows) > imp_consts.MAX_ROWS + 1:
                break
    except ElementTree.ParseError:
        return fail("That worksheet is not valid XML.")
    return {"error": None, "content": rows}
