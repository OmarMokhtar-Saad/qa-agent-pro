"""Diagnostics screen-capture table: one row per stored screen."""

from __future__ import annotations

from tools.mobile.report_text import _text, esc

#: The screen-capture fields Diagnostics tabulates, in this order.
CAPTURE_FIELDS = (
    "dropped_inside_panel",
    "dropped_outside_panel",
    "dropped_unclassified",
    "considered",
    "panel_axes",
    "dialog_package",
)


def _capture_value(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return " × ".join(_text(v, 12) for v in value)
    return _text(value, 60)


def _capture_rows(book: dict) -> list:
    return [
        (str(sid), {k: _capture_value(s.get(k)) for k in CAPTURE_FIELDS})
        for sid, s in sorted(book.items())
        if isinstance(s, dict)
    ]


def _capture_head(cols: list) -> str:
    return "<th>screen</th>" + "".join(
        "<th>" + esc(k.replace("_", " "), 40) + "</th>" for k in cols
    )


def _capture_body(rows: list, cols: list) -> str:
    return "".join(
        "<tr><td>"
        + esc(sid, 40)
        + "</td>"
        + "".join("<td>" + esc(r[k], 60) + "</td>" for k in cols)
        + "</tr>"
        for sid, r in rows
    )


# One row per stored screen; a column no screen fills is left out.
def _capture_table(screens: object) -> str:
    book = screens if isinstance(screens, dict) else {}
    rows = _capture_rows(book)
    cols = [k for k in CAPTURE_FIELDS if any(r[k] not in ("", "0") for _sid, r in rows)]
    if not rows or not cols:
        return ""
    return (
        '<div class="tablewrap"><table class="cov"><thead><tr>'
        + _capture_head(cols)
        + "</tr></thead><tbody>"
        + _capture_body(rows, cols)
        + "</tbody></table></div>"
    )
