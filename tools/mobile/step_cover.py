"""Which part of a step's wall time the timing ledger accounts for.

Phases NEST (``evidence`` holds adb calls, ``ui_dump`` holds the tree source), so
their milliseconds cannot be summed against the step total. This keeps each
timed span as an interval and reports the UNION: the time not inside any span
is what the ledger could not name, and :func:`itemised` adds it as one
``unitemised`` row so the ledger always closes to the total.
"""

from __future__ import annotations

import time

from tools.mobile import step_detail

#: Spans kept per step; past it they are compacted into their union and, if still
#: more, the oldest disjoint ones fold into a running total (never dropped).
MAX_SPANS = 400

#: Ledger rows kept before the closing row is appended; step_ledger folds older
#: rows by reason+caller so its list never exceeds this.
KEEP_ROWS = 39

#: Ledger reason for the wall time no span covers.
UNITEMISED = "unitemised"


def span(state: dict, seconds: float, name: str = "") -> None:
    """Remember that a timed phase *name* just ended, *seconds* long."""
    end = time.monotonic()
    one = (end - max(0.0, seconds), end)
    for key, spans in (
        ("*", state.setdefault("spans", [])),
        (name, _named(state, name)),
    ):
        spans.append(one)
        if len(spans) > MAX_SPANS:
            _compact(state, key, spans)


def _compact(state: dict, key: str, spans: list) -> None:
    """Replace *spans* by their union; fold the oldest into ``folded[key]`` past the cap."""
    merged: list = []
    for begin, end in sorted(spans):
        if merged and begin <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((begin, end))
    folded = state.setdefault("folded", {})
    while len(merged) > MAX_SPANS:
        begin, end = merged.pop(0)
        folded[key] = folded.get(key, 0.0) + (end - begin)
    spans[:] = merged


def _named(state: dict, name: str) -> list:
    """The spans of one phase (a throw-away list when it has no name)."""
    return state.setdefault("pspans", {}).setdefault(name, []) if name else []


def _union_s(spans: list, low: float = 0.0) -> float:
    reached, total = low, 0.0
    for begin, end in sorted(spans):
        begin = max(begin, reached)
        if end > begin:
            total += end - begin
            reached = end
    return total


def phase_ms(state: dict, name: str, total: float) -> int:
    """Wall milliseconds of phase *name*: calls that ran at the same time count
    once, so a phase never reads longer than the step that held it (P6)."""
    spans = (state.get("pspans") or {}).get(name)
    if not spans:
        return int(total * 1000)
    low = float(state.get("start", 0.0))
    return int((_union_s(spans, low) + _folded(state, name)) * 1000)


def phases(state: dict, whole: int) -> dict:
    """``{name: {"ms", "n"}}`` of every phase, none longer than the step's *whole* ms."""
    return {
        name: {"ms": min(whole, phase_ms(state, name, total)), "n": count}
        for name, (total, count) in state["phases"].items()
    }


def _folded(state: dict, key: str) -> float:
    """Seconds of spans already folded out of *key*'s list."""
    return float((state.get("folded") or {}).get(key, 0.0))


def covered_ms(state: dict) -> int:
    """Milliseconds of the step inside at least one span (the union)."""
    union = _union_s(state.get("spans") or [], float(state.get("start", 0.0)))
    return int((union + _folded(state, "*")) * 1000)


def itemised(state: dict, total_ms: int) -> list:
    """The ledger rows plus one ``unitemised`` row for the uncovered time."""
    detail = step_detail.rows(state)
    rows = list(state.get("ledger") or [])[-(KEEP_ROWS - len(detail)) :]
    gap = max(0, int(total_ms) - covered_ms(state))
    return rows + detail + [{"reason": UNITEMISED, "ms": gap, "skipped": False}]
