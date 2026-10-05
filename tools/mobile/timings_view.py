"""Per-step timings for a run's summary and HTML report.

Pure and read-only: rows in, a dict and two strings out. Every device-derived
value that reaches HTML goes through ``html.escape``, and the HTML carries no
external assets.
"""

from __future__ import annotations

import html

from tools.mobile import step_timing

#: How many of the slowest steps the summary names.
SLOWEST_SHOWN = 3


def _parse(rows: object) -> list:
    out = []
    for row in list(rows or [])[-step_timing.MAX_STEP_TIMING_ROWS :]:
        parsed = (
            row
            if isinstance(row, step_timing.StepTiming)
            else step_timing.StepTiming.from_dict(row)
        )
        if parsed is not None:
            out.append(parsed)
    return out


def summarize(rows: object) -> dict:
    """Totals, the slowest steps, the share of time spent dumping, and the rows."""
    parsed = _parse(rows)
    total = sum(r.duration_ms for r in parsed)
    dump_ms = sum(r.dump_ms for r in parsed)
    slowest = sorted(parsed, key=lambda r: r.duration_ms, reverse=True)
    return {
        "steps": len(parsed),
        "total_ms": total,
        "dump_ms": dump_ms,
        "wait_ms": sum(r.wait_ms for r in parsed),
        "dumps": sum(r.dumps for r in parsed),
        "dump_share": round(dump_ms / total, 2) if total else 0.0,
        "slowest": [r.to_dict() for r in slowest[:SLOWEST_SHOWN]],
        "rows": [r.to_dict() for r in parsed],
    }


def _seconds(ms: object) -> str:
    try:
        return "{:.1f} s".format(int(ms) / 1000.0)
    except (TypeError, ValueError, OverflowError):
        return "0.0 s"


def summary_line(summary: object) -> str:
    """One line for the run summary, or an empty string without steps."""
    data = summary if isinstance(summary, dict) else {}
    if not data.get("steps"):
        return ""
    line = "{} steps took {}; {} screen dumps used {} ({}% of the time)".format(
        int(data.get("steps") or 0),
        _seconds(data.get("total_ms")),
        int(data.get("dumps") or 0),
        _seconds(data.get("dump_ms")),
        int(round(float(data.get("dump_share") or 0.0) * 100)),
    )
    slow = (data.get("slowest") or [{}])[0]
    return line + "; slowest was step {} at {}".format(
        int(slow.get("index", 0)), _seconds(slow.get("duration_ms"))
    )


def _row_html(row: dict) -> str:
    cells = [
        str(row.get("index", "")),
        _seconds(row.get("duration_ms")),
        str(row.get("dumps", "")),
        _seconds(row.get("dump_ms")),
        _seconds(row.get("wait_ms")),
        str(row.get("source", "")),
    ]
    return "<tr>" + "".join("<td>" + html.escape(c) + "</td>" for c in cells) + "</tr>"


def html_section(summary: object) -> str:
    """A self-contained, escaped HTML section; empty without steps."""
    data = summary if isinstance(summary, dict) else {}
    if not data.get("steps"):
        return ""
    head = "".join(
        "<th>" + html.escape(h) + "</th>"
        for h in ("Step", "Duration", "Dumps", "Dump time", "Wait time", "Source")
    )
    rows = "".join(_row_html(r) for r in data.get("rows") or [] if isinstance(r, dict))
    return (
        '<section class="timings"><h2>Step timings</h2><p>'
        + html.escape(summary_line(data))
        + "</p><table><thead><tr>"
        + head
        + "</tr></thead><tbody>"
        + rows
        + "</tbody></table></section>"
    )
