"""The run sequence: one list item per action, folded with its screen change."""

from __future__ import annotations

from dataclasses import dataclass

from tools.mobile.report_text import esc, fmt_ms
from tools.mobile.report_widgets import _outcome_pill, _PageData
from tools.mobile.rfrm_phone import _phone_html, _PhoneHead
from tools.mobile.rtrc_rows import _changed

#: The row kind a trace op is drawn as. The kinds are the shell's own vocabulary
#: (``.seq.is-<kind>`` is what the stylesheet colours by).
OP_KIND = {
    "tap": "tap",
    "press": "tap",
    "back": "tap",
    "type": "step",
    "set": "step",
    "clear": "step",
    "swipe": "step",
    "scroll": "step",
    "assert": "event",
    "wait": "log",
    "wait_until_text": "log",
    "wait_until_gone": "log",
    "wait_until_changed": "log",
    "wait_until_idle": "log",
    "done": "done",
    "escape": "note",
    "ask": "note",
    "needs": "note",
}


@dataclass(frozen=True)
class _SeqParts:
    """The three markup pieces of one sequence item: head, duration bar, detail."""

    head: str
    bar: str
    detail: str


# A row whose screen CHANGED carries the new screen's frame in its fold, so
# the sequence reads as what the tester would have seen, step by step.
def _seq_rows(rows: list, screens: object = None, app: str = "") -> str:
    """The run sequence: one row per action, in the order it ran."""
    if not rows:
        return ""
    longest = max((row["ms"] or 0) for row in rows) or 0
    clock = 0
    out = []
    for row in rows:
        kind = OP_KIND.get(row["op"].lower(), "step")
        label = "0.0s" if clock == 0 else "+%.1fs" % (clock / 1000.0)
        parts = _SeqParts(
            _seq_head(row), _seq_bar(row, longest), _seq_detail(row, screens, app)
        )
        out.append(_seq_item(kind, label, parts))
        clock += row["ms"] or 0
    return '<ul class="seqlist">' + "".join(out) + "</ul>"


def _seq_bar(row: dict, longest: int) -> str:
    """The step's duration bar against the longest step, or '' without a time."""
    if row["ms"] is None or not longest:
        return ""
    return (
        '<span class="seqbar k-tool" title="'
        + str(int(row["ms"]))
        + " ms of the "
        + str(int(longest))
        + ' ms longest step in this case"><i style="width:%.1f%%"></i></span>'
        % max(3.0, min(100.0, row["ms"] / float(longest) * 100.0))
    )


def _seq_head(row: dict) -> str:
    """The step's operation, action text, time and outcome pill."""
    return (
        '<span class="seqk">'
        + esc(row["op"], 24)
        + '</span><span class="seqtx" dir="auto">'
        + esc(row["action"], 160)
        + "</span>"
        + (
            ('<span class="mt">' + esc(fmt_ms(row["ms"]), 16) + "</span>")
            if row["ms"] is not None
            else ""
        )
        + _outcome_pill(row["outcome"])
    )


def _after_phone(row: dict, screens: object, app: str) -> str:
    """The phone showing the screen a step left behind."""
    look = _PhoneHead(
        "Screen after",
        "what this action left on screen",
        row["after_obs"] or row["after"],
    )
    return _phone_html(look, _PageData(library=screens, app=app))


def _seq_detail(row: dict, screens: object, app: str) -> str:
    """The folded detail of a step: full text, screen change, new screen frame."""
    detail = ""
    if row["detail"]:
        detail = '<p class="seqfull" dir="auto">' + esc(row["detail"]) + "</p>"
    if row["before"] or row["after"]:
        detail += (
            '<div class="chipnote"><i>screen</i>'
            + (esc(row["before"], 40) or "—")
            + " → "
            + (esc(row["after"], 40) or "—")
            + "</div>"
        )
    if row["after"] and _changed(row):
        detail += '<div class="phonewide">' + _after_phone(row, screens, app) + "</div>"
    return detail


def _seq_item(kind: str, label: str, parts: _SeqParts) -> str:
    """One sequence list item: a fold when there is detail, else a plain row."""
    top = '<li class="seq is-' + kind + '">'
    main = '<span class="seqt">' + label + '</span><div class="seqmain">' + parts.head
    main += "</div>" + parts.bar
    if parts.detail:
        return (
            top
            + '<details class="seqfold"><summary><span class="chev" aria-hidden="true"></span>'
            + main
            + '</summary><div class="seqdetail">'
            + parts.detail
            + "</div></details></li>"
        )
    return top + '<span class="chev ghost" aria-hidden="true"></span>' + main + "</li>"
