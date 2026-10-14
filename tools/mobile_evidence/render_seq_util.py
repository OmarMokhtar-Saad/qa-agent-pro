"""Sequence helpers: the duration bar, the sort key and the case clock."""

from __future__ import annotations

from tools.mobile_evidence.render_esc import ev_esc
from tools.mobile_evidence.render_read import flatten_case


def bar(ms: object, longest: float, kind: str) -> str:
    if not isinstance(ms, (int, float)) or ms < 0 or not longest:
        return ""
    return (
        '<span class="seqbar k-'
        + ev_esc(kind, 12)
        + '" title="'
        + str(int(ms))
        + " ms of the "
        + str(int(longest))
        + ' ms longest step in this case"><i style="width:%.1f%%"></i></span>'
        % max(3.0, min(100.0, ms / float(longest) * 100.0))
    )


def seq_key(rec: dict) -> float | None:
    ts = rec.get("ts")
    return float(ts) if isinstance(ts, (int, float)) else None


def clock_offset(case: dict) -> int:
    offset = flatten_case(case).get("clock_offset_ms")
    return (
        int(offset)
        if isinstance(offset, (int, float)) and not isinstance(offset, bool)
        else 0
    )


def longest_step_ms(view: dict) -> float:
    durations = []
    for turn in view["turns"]:
        for stream in ("llm", "bindings", "tools"):
            for rec in turn.get(stream) or []:
                ms = rec.get("derivedMs")
                if isinstance(ms, (int, float)):
                    durations.append(float(ms))
    return max(durations) if durations else 0.0
