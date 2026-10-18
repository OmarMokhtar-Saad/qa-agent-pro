"""Fold the oldest ledger rows of a step into one row per (reason, caller).

A step can make dozens of dumps; a plain "newest N" cap hid all but the last few.
Folding keeps the newest rows verbatim and sums the older ones by what asked for
them, so the total time and count per reason stay visible. Pure; never raises.
"""

from __future__ import annotations

#: Newest rows kept verbatim when a ledger overflows.
RECENT_ROWS = 20


def _key(row: dict) -> tuple:
    return (row.get("reason"), row.get("caller"), bool(row.get("skipped")))


def _merge(into: dict, row: dict) -> None:
    into["ms"] = int(into.get("ms") or 0) + int(row.get("ms") or 0)
    into["n"] = int(into.get("n") or 1) + int(row.get("n") or 1)


def _aggregate(rows: list) -> list:
    """One row per key, in first-seen order; ``n`` counts the rows folded in."""
    merged: dict = {}
    for row in rows:
        got = merged.get(_key(row))
        if got is None:
            merged[_key(row)] = dict(row, n=int(row.get("n") or 1))
        else:
            _merge(got, row)
    return list(merged.values())


def fold(rows: list, cap: int) -> list:
    """*rows* when within *cap*, else older rows folded so the list fits."""
    if len(rows) <= cap:
        return rows
    old, recent = rows[:-RECENT_ROWS], rows[-RECENT_ROWS:]
    folded = _aggregate(old)
    room = max(0, cap - 1 - len(recent))
    if len(folded) > room:
        folded = sorted(folded, key=lambda r: -int(r.get("ms") or 0))[:room]
    return folded + recent
