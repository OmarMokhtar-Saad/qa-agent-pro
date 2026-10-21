"""Claim-vs-evidence table for the final packet (B21).

Every case's verdict is listed with the proof the server holds (assert ops and
screen ids from its trace) or ``no evidence``. The host quotes this table; it
does not add claims the table lacks.
"""

from __future__ import annotations

from tools.mobile import finish_events, run_cases

HEADER = (
    "\n\nClaim vs evidence (quote this table; state no check it lists as "
    "`no evidence`):\n| case | verdict | proof |\n|---|---|---|"
)


def _trace(case: object) -> list:
    trace = case.get("trace") if isinstance(case, dict) else None
    return [e for e in trace if isinstance(e, dict)] if isinstance(trace, list) else []


def _op(entry: dict) -> str:
    """The op of a trace entry: nested ``action.op``, else a top-level ``op``."""
    action = entry.get("action")
    nested = action.get("op") if isinstance(action, dict) else None
    return str(nested or entry.get("op") or "")


def _is_assert(entry: dict) -> bool:
    """A passed assert; an entry without an outcome counts as passed (old shape)."""
    outcome = str(entry.get("outcome") or "assert_pass")
    return _op(entry) == "assert" and outcome in ("assert_pass", "ok")


def _screen_ids(trace: list) -> list:
    keys = ("after_screen_id", "screen")
    return sorted({str(e[k]) for e in trace for k in keys if e.get(k)})


def _proof(case: object) -> str:
    trace = _trace(case)
    asserts = [e for e in trace if _is_assert(e)]
    if not asserts:
        return "no evidence"
    screens = _screen_ids(trace)
    return f"{len(asserts)} assert(s)" + (
        f", screens {', '.join(screens)}" if screens else ""
    )


def _row_line(row: dict, case: object) -> str:
    verdict = str(row.get("verdict") or row.get("status") or "unknown")
    return f"| {row.get('tc_id', '?')} | {verdict} | {_proof(case)} |"


def evidence_table(run_id: str, rows: list) -> str:
    """The table text, or ``""`` when the run has no case rows."""
    lines = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("tc_id"):
            continue
        case = (run_cases.read_case(run_id, str(row["tc_id"])) or {}).get("content")
        lines.append(_row_line(row, case))
    if not lines:
        return ""
    events = finish_events.events_line(run_id)
    return HEADER + "\n" + "\n".join(lines) + ("\n\n" + events if events else "")
