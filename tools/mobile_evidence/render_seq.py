"""The merged Run sequence: app records and the lane's own actions on one clock."""

from __future__ import annotations

from typing import NamedTuple, cast

from tools.mobile_evidence import exchanges
from tools.mobile_evidence.render_esc import MAX_ROWS, guard
from tools.mobile_evidence.render_html import pill
from tools.mobile_evidence.render_seq_agent import agent_items
from tools.mobile_evidence.render_seq_log import flow_items, runlog_items
from tools.mobile_evidence.render_seq_turn import (
    call_items,
    llm_items,
    turn_opening_items,
)
from tools.mobile_evidence.render_seq_util import clock_offset, longest_step_ms
from tools.mobile_evidence.render_view import content_of, has_evidence, view_of


class Trace(NamedTuple):
    rows: list | None
    frames: dict | None


def _step_inner(op: str, line: str, outcome: str) -> str:
    step = exchanges.seq_step(op, line)
    if not outcome:
        return step + ""
    ok = outcome in ("ok", "done") or outcome.endswith("_pass")
    return step + pill("p-ok" if ok else "p-gap", outcome)


def _trace_item(pair: tuple, offset: int, t: Trace) -> tuple | None:
    i, entry = pair
    at = entry.get("at")
    if not isinstance(at, (int, float)) or isinstance(at, bool) or at <= 0:
        return None
    key = float(at) * 1000.0 + offset
    rows = cast(list, t.rows)
    row = rows[i] if i < len(rows) and isinstance(rows[i], dict) else {}
    op = str(row.get("op") or (entry.get("action") or {}).get("op") or "?")
    line = str(row.get("action") or op)
    outcome = str(row.get("outcome") or entry.get("outcome") or "")
    kind = "tap" if op in ("tap", "press", "back") else "step"
    inner = _step_inner(op, line, outcome)
    frame = (t.frames or {}).get(i) or ""
    return (key, exchanges.SEQ_RANK[kind], kind, guard(inner) + str(frame))


def _trace_items(case: dict, offset: int, t: Trace) -> list:
    """The lane's own actions, on the same clock."""
    trace = [x for x in (case.get("trace") or [])[:MAX_ROWS] if isinstance(x, dict)]
    t = Trace(list(t.rows or []), t.frames)
    found = [_trace_item(pair, offset, t) for pair in enumerate(trace)]
    return [item for item in found if item is not None]


def _turn_items(view: dict, longest: float) -> list:
    items: list = []
    multi = len(view["turns"]) > 1
    for n, turn in enumerate(view["turns"], 1):
        items += turn_opening_items(turn, n, multi)
        items += llm_items(turn, longest)
        items += call_items(turn, longest)
        items += runlog_items(turn)
        items += flow_items(turn)
        items += agent_items(turn)
    return items


def sequence_items(
    loaded: object,
    tc_id: object,
    trace: Trace | None = None,
) -> list:
    """``[(key, rank, kind, inner_html, clock_label, bar)]`` for ``exchanges.seqlist``.

    *trace.frames* is ``{trace index: frame html}``; it is this repository's own
    markup, so it is appended OUTSIDE ``guard``. App records (device epoch ms)
    and lane actions (host ``at`` plus the case's clock offset) share one clock.
    """
    content = content_of(loaded)
    view = view_of(loaded, tc_id)
    if not has_evidence(loaded) or not view:
        return []
    case = (content.get("cases_by_id") or {}).get(str(tc_id or "")) or {}
    offset = clock_offset(case)
    items = _turn_items(view, longest_step_ms(view))
    trace = trace or Trace(None, None)
    return items + _trace_items(case, offset, trace)
