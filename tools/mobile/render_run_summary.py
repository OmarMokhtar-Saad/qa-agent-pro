"""The end-of-run (or mid-run) summary block.

Sixteenth layer of the ``render_*`` split of ``render.py``.

``summary_block`` -- the end-of-run (or mid-run) summary, built from
checkpoints only.

One line per case, so a 200-case run is 200 lines rather than 200 sections.
The tail delegates to :func:`render_report.report_line`, which is the only thing
in this lane that may mention the HTML report at all.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from tools.mobile.render_report import _ReportRef, report_line
from tools.mobile.render_status_word import status_label
from tools.mobile.render_tally import _retry_note, _typed_field_tally
from tools.mobile.render_verdict import verdict_line


def _summary_head(abandoned: bool, partial: bool, run_id: str) -> str:
    return (
        "## Mobile run "
        + ("abandoned" if abandoned else ("progress" if partial else "finished"))
        + (" — `" + str(run_id) + "`" if run_id else "")
        + "\n\n"
    )


def _tally_text(rows: list[dict]) -> str:
    tally: dict[str, int] = {}
    for row in rows:
        key = status_label(row) or "unknown"
        tally[key] = tally.get(key, 0) + 1
    return ", ".join(str(count) + " " + name for name, count in sorted(tally.items()))


def _summary_body(rows: list[dict], coverage_line: str) -> str:
    counts = _tally_text(rows) or "no cases recorded yet"
    cover = (coverage_line + "\n\n") if coverage_line else ""
    return cover + counts + "\n\n" + _typed_field_tally(rows)


# The tally counts a verdict OR a status under one heading, which is how
# "3 done" came to sit three lines under "0 done, 3 remaining of 3": a
# status was standing in for a verdict in one count and not in the other.
# The coverage sentence goes first, from the one producer, so a status
# count can never be read as a verdict count.
class SummaryOpts(NamedTuple):
    """The optional inputs of ``summary_block``, grouped (all default)."""

    coverage_line: str = ""
    run_id: str = ""
    partial: bool = False
    abandoned: bool = False
    report_path: str = ""
    report_opened: bool = False
    report_error: str = ""


def summary_block(cases: Any, opts: SummaryOpts = SummaryOpts()) -> str:
    """The end-of-run (or mid-run) summary, built from checkpoints only."""
    rows = [row for row in list(cases or []) if isinstance(row, dict)]
    head = _summary_head(opts.abandoned, opts.partial, opts.run_id)
    body = _summary_body(rows, opts.coverage_line)
    lines = "\n".join(verdict_line(row) for row in rows)
    ref = _ReportRef(
        opts.report_path, opts.partial, opts.report_opened, opts.report_error
    )
    tail = "\n\n" + report_line(ref)
    return head + body + lines + _retry_note(rows, partial=opts.partial) + tail
