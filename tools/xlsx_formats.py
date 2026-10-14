"""Cell formats for the 'Test Cases' sheet."""

from __future__ import annotations

from typing import NamedTuple

import xlsxwriter


class _CaseFormats(NamedTuple):
    """Every cell format the 'Test Cases' sheet writes with."""

    header: xlsxwriter.format.Format
    even: xlsxwriter.format.Format
    odd: xlsxwriter.format.Format
    even_rtl: xlsxwriter.format.Format
    odd_rtl: xlsxwriter.format.Format
    status_even: xlsxwriter.format.Format
    status_odd: xlsxwriter.format.Format
    priority_fill: dict


def _priority_fill_formats(workbook: xlsxwriter.Workbook) -> dict:
    # Direct (baked-in) fills for the Priority column. Apple Numbers drops Excel
    # *conditional* formatting on import, so the value-based colors of the
    # conditional rules would vanish there. Writing the color straight onto the
    # cell makes it show in Numbers too. The conditional_format rules are kept,
    # so Excel / LibreOffice / Google Sheets still re-color live when a Priority
    # is changed (a conditional format overrides the direct fill in those apps).
    fills = {}
    for name, bg, font, bold in (
        ("Critical", "#FF4444", "#FFFFFF", True),
        ("High", "#FFC7CE", "#9C0006", False),
        ("Medium", "#FFEB9C", "#9C6500", False),
        ("Low", "#C6EFCE", "#006100", False),
    ):
        props = {"bg_color": bg, "font_color": font, "border": 1}
        if bold:
            props["bold"] = True
        fills[name] = workbook.add_format({**props, "valign": "top", "text_wrap": True})
    return fills


def _apply_rtl(props: dict) -> None:
    # xlsxwriter's documented `reading_order` format property emits
    # readingOrder="2" (plus horizontal="right") into the cell
    # alignment element of xl/styles.xml. Without it Excel lays an
    # Arabic-majority cell out left-to-right even though the string
    # itself is correct, and the tester blames the generator. No
    # monkeypatch or OOXML post-patching is needed -- verified
    # against xlsxwriter 3.2.9 and asserted at the OOXML level (not
    # by visual rendering) in tests/test_bilingual_rules.py.
    props["reading_order"] = 2
    props["align"] = "right"


def _case_cell_format(
    workbook: xlsxwriter.Workbook, bg: str, rtl: bool = False
) -> xlsxwriter.format.Format:
    props = {
        "bg_color": bg,
        "border": 1,
        "valign": "top",
        "text_wrap": True,
    }
    if rtl:
        _apply_rtl(props)
    return workbook.add_format(props)


def _case_header_format(workbook: xlsxwriter.Workbook) -> xlsxwriter.format.Format:
    return workbook.add_format(
        {
            "bold": True,
            "font_color": "#FFFFFF",
            "bg_color": "#1F4E79",
            "border": 1,
            "align": "center",
            "valign": "vcenter",
            "text_wrap": True,
        }
    )


def _status_base_format(workbook: xlsxwriter.Workbook) -> xlsxwriter.format.Format:
    # Status column needs its own base formats: centered + no bg so conditional format colors show
    return workbook.add_format(
        {
            "bg_color": "#D9D9D9",
            "font_color": "#595959",
            "border": 1,
            "align": "center",
            "valign": "vcenter",
        }
    )


def _build_case_formats(workbook: xlsxwriter.Workbook) -> _CaseFormats:
    header_fmt = _case_header_format(workbook)
    even_fmt = _case_cell_format(workbook, "#FFFFFF")
    odd_fmt = _case_cell_format(workbook, "#EBF3FB")
    even_rtl_fmt = _case_cell_format(workbook, "#FFFFFF", rtl=True)
    odd_rtl_fmt = _case_cell_format(workbook, "#EBF3FB", rtl=True)
    _pri_fill = _priority_fill_formats(workbook)
    status_odd_fmt = _status_base_format(workbook)
    status_even_fmt = _status_base_format(workbook)
    return _CaseFormats(
        header=header_fmt,
        even=even_fmt,
        odd=odd_fmt,
        even_rtl=even_rtl_fmt,
        odd_rtl=odd_rtl_fmt,
        status_even=status_even_fmt,
        status_odd=status_odd_fmt,
        priority_fill=_pri_fill,
    )
