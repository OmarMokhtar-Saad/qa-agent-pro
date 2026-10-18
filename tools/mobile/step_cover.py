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

#: Spans kept per step; past it the oldest are dropped (a step has a handful).
MAX_SPANS = 400

#: Ledger rows kept before the closing row is appended; step_ledger folds older
#: rows by reason+caller so its list never exceeds this.
KEEP_ROWS = 39

#: Ledger reason for the wall time no span covers.
UNITEMISED = "unitemised"


def span(state: dict, seconds: float) -> None:
    """Remember that a timed phase just ended, *seconds* long."""
    end = time.monotonic()
    spans = state.setdefault("spans", [])
    spans.append((end - max(0.0, seconds), end))
    del spans[:-MAX_SPANS]


def covered_ms(state: dict) -> int:
    """Milliseconds of the step inside at least one span (the union)."""
    low = float(state.get("start", 0.0))
    reached, total = low, 0.0
    for begin, end in sorted(state.get("spans") or []):
        begin = max(begin, reached)
        if end > begin:
            total += end - begin
            reached = end
    return int(total * 1000)


def itemised(state: dict, total_ms: int) -> list:
    """The ledger rows plus one ``unitemised`` row for the uncovered time."""
    detail = step_detail.rows(state)
    rows = list(state.get("ledger") or [])[-(KEEP_ROWS - len(detail)) :]
    gap = max(0, int(total_ms) - covered_ms(state))
    return rows + detail + [{"reason": UNITEMISED, "ms": gap, "skipped": False}]
