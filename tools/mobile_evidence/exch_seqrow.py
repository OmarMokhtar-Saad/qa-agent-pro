"""The sequence row, its session opener and the ordered list that holds them."""

from __future__ import annotations

from tools.mobile_evidence.exch_seqargs import SeqRow, SessionParts
from tools.mobile_evidence.exch_seqk import SEQ_CUT
from tools.mobile_shared.scrub import e


def _row_classes(kind: str) -> str:
    """The class list of a sequence row."""
    classes = "seq is-" + e(kind)
    if kind == "reply":
        classes += (
            " is-sara"  # the shell colours the app's reply row under this class name
        )
    return classes


def _row_main(clock: str, head: str, bar: str) -> str:
    """The clock, the one line and the bar every row draws."""
    return (
        '<span class="seqt">'
        + e(clock)
        + '</span><div class="seqmain">'
        + head
        + "</div>"
        + bar
    )


def _row_flat(classes: str, main: str) -> str:
    """A row with nothing behind it: a ghost chevron holds the slot."""
    return (
        '<li class="'
        + classes
        + '"><span class="chev ghost" aria-hidden="true"></span>'
        + main
        + "</li>"
    )


def _row_fold(classes: str, main: str, detail: str) -> str:
    """A row with a fold: the whole line is the summary."""
    return (
        '<li class="'
        + classes
        + '"><details class="seqfold"><summary><span class="chev" aria-hidden="true"></span>'
        + main
        + '</summary><div class="seqdetail">'
        + detail
        + "</div></details></li>"
    )


def seq_row(row: SeqRow) -> str:
    """One row of the sequence: chevron slot, clock, kind, one line, then its own pills and
    bar -- and the WHOLE line is the click target. A builder with more to show appends
    ``SEQ_CUT`` and the detail after it; a row with nothing behind it draws a ghost chevron in
    the same slot so lines start at the same x."""
    head, _cut, detail = row.inner.partition(SEQ_CUT)
    classes = _row_classes(row.kind)
    main = _row_main(row.clock, head, row.bar)
    if not detail.strip():
        return _row_flat(classes, main)
    return _row_fold(classes, main, detail)


def seq_session(note: str, parts: SessionParts | None = None) -> str:
    parts = parts or SessionParts()
    label, extra = parts.label, parts.extra
    return seq_row(
        SeqRow(
            "start",
            "0.0s",
            ('<span class="seqk">' + e(label) + "</span>" if label else "")
            + ('<span class="mt">' + note + "</span>" if note else "")
            + parts.pill_html
            + ((SEQ_CUT + extra) if extra else ""),
        )
    )


def ms_clock(key: float | None, first: float | None) -> str:
    """Relative to the first recorded thing: the absolute epoch is noise, the GAP is not."""
    if key is None or first is None:
        return ""
    rel = key - first
    return ("+%.1fs" % (rel / 1000.0)) if rel else "0.0s"


def no_clock(key: object, first: object) -> str:
    """For records that carry an ORDER but no clock: printing +0.0s would invent a measurement."""
    return ""


def seqlist(items: list, session: str | None = None, clock=ms_clock) -> str:
    """Merge everything witnessed into one ordered stream.

    *items* is ``[(key, rank, kind, inner_html[, clock_label[, bar_html]])]``; *key* sorts
    and *rank* breaks ties inside it. A fifth element overrides the clock for that row alone,
    a sixth is its waterfall bar; both optional, for the same reason: a report that timed
    nothing passes none and draws none.
    """
    items = [item for item in items if item[3]]
    if not items:
        return ""
    items.sort(key=lambda item: (item[0], item[1]))
    first = items[0][0]
    rows = [session] if session else []
    for item in items:
        key, _rank, kind, inner = item[:4]
        label = item[4] if len(item) > 4 and item[4] is not None else clock(key, first)
        bar = item[5] if len(item) > 5 and item[5] else ""
        rows.append(seq_row(SeqRow(kind, label, inner, bar)))
    return '<ul class="seqlist">' + "".join(rows) + "</ul>"
