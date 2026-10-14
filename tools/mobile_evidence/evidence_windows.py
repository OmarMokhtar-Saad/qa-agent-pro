"""One device-clock window per case, and the fence of app runs around them."""

from __future__ import annotations

from tools.mobile_evidence.evidence_notes import (
    INVERTED_NOTE,
    NO_CLOCK_NOTE,
    TOLERANCE_MS,
)


def _ms(value):
    """A host ``time.time()`` (seconds) or an epoch-ms value, as epoch ms, or None."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number <= 0 or number != number:  # NaN
        return None
    # A seconds stamp is ~1.7e9; an ms stamp ~1.7e12.
    #
    # CLAMPED before the second coercion, exactly as the twin
    # `tools.mobile.report._ms` already did. The guard above admits an
    # infinity -- `float(inf)` does not raise -- and `int(inf)` then
    # raised OUT of this function, which a run-store checkpoint carrying
    # `Infinity` reaches through `case_windows`. Widening the except
    # tuple could not have caught it: the coercion is not in the try.
    number = min(number, 1e15)
    return int(number * 1000) if number < 1e11 else int(number)


def _no_window_row(case):
    """The row of a case that carries no start or end time."""
    return {
        "tc_id": str(case.get("tc_id") or ""),
        "lo": None,
        "hi": None,
        "exact": False,
        "clipped": False,
        "note": "this case carries no start or end time, so no window can be drawn",
    }


def _timed_row(case, span, tolerance_ms):
    """The widened window row of a case with a known ``(started, updated)`` span."""
    started, updated = span
    offset = case.get("clock_offset_ms")
    exact = isinstance(offset, (int, float)) and not isinstance(offset, bool)
    return {
        "tc_id": str(case.get("tc_id") or ""),
        "started": started,
        "updated": updated,
        "lo": started + (int(offset) if exact else -tolerance_ms),
        "hi": updated + (int(offset) if exact else tolerance_ms),
        "exact": exact,
        "clipped": False,
        "note": "" if exact else NO_CLOCK_NOTE,
    }


def _window_row(case, tolerance_ms):
    """The raw (unclipped) window row for one case dict."""
    started = _ms(case.get("started"))
    updated = _ms(case.get("updated"))
    if started is None and updated is None:
        return _no_window_row(case)
    started = started if started is not None else updated
    updated = updated if updated is not None else started
    if updated < started:
        started, updated = updated, started
    return _timed_row(case, (started, updated), tolerance_ms)


def _clip_to_neighbours(timed, i, row):
    """Clip one widened window at the midpoint to each neighbour."""
    if i > 0:
        prev = timed[i - 1]
        mid = (prev["updated"] + row["started"]) // 2
        if row["lo"] < mid:
            row["lo"], row["clipped"] = mid, True
    if i + 1 < len(timed):
        nxt = timed[i + 1]
        mid = (row["updated"] + nxt["started"]) // 2
        if row["hi"] > mid:
            row["hi"], row["clipped"] = mid, True


def _clamp_inverted(row):
    """Nested cases (one started and ended inside another) clip to lo > hi: an
    inverted window matches nothing and reads as "the app did nothing". Clamp
    to a point and SAY so, rather than joining nothing in silence."""
    row["inverted"] = False
    if row["hi"] < row["lo"]:
        row["hi"], row["inverted"] = row["lo"], True
        row["note"] = (row["note"] + " " if row["note"] else "") + INVERTED_NOTE


def case_windows(cases, tolerance_ms=TOLERANCE_MS):
    """One device-clock window per case, exact when the offset is known, clipped when not.

    Returns a list sorted by ``started`` of ``{tc_id, lo, hi, exact, clipped, note}``.
    A case with no usable ``started``/``updated`` has ``lo``/``hi`` None and joins nothing.
    """
    rows = [_window_row(c, tolerance_ms) for c in cases or [] if isinstance(c, dict)]
    timed = sorted((r for r in rows if r["lo"] is not None), key=lambda r: r["started"])
    # Clip the WIDENED windows at the midpoint to each neighbour, so two windows never
    # overlap. An exact window is never widened, so it never needs clipping -- but a
    # widened neighbour may reach into it, and that side IS clipped.
    for i, row in enumerate(timed):
        if row["exact"]:
            continue
        _clip_to_neighbours(timed, i, row)
        _clamp_inverted(row)
    return timed + [r for r in rows if r["lo"] is None]


def fence(report, window):
    """Which app runs the run window touches (overlap, not containment), and which it does not."""
    runs = report.get("appRuns") or []
    lo, hi = (window or {}).get("from"), (window or {}).get("to")
    if lo is None or hi is None:
        return (
            list(runs),
            [],
            "no case in this run carries a window, so every app run in the capture is shown",
        )
    inside, outside = [], []
    for r in runs:
        a, b = r.get("firstTs"), r.get("lastTs")
        if a is None or b is None:
            outside.append(r)
            continue
        (inside if (a <= hi and b >= lo) else outside).append(r)
    return inside, outside, ""
