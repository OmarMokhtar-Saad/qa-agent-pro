"""The run verdict of an explore (goal) run: its last explicit ``done`` decides.

A suite verdict needs EVERY case to pass. An explore run is one goal worked in
turns, and most turns end mid-goal (a wait that timed out, a field the model
mistyped and retried), so those rows never carry a verdict. When the final turn
ended in ``done(pass)`` with its assert green, those earlier stumbles are
warnings in the report, not the run's result. An explicit ``fail`` row is never
overridden.
"""

from __future__ import annotations

from tools.mobile.verdicts import VERDICT_FAIL, VERDICT_PASS


def _verdict(row: object) -> str:
    return str(row.get("verdict") or "") if isinstance(row, dict) else ""


def _turn_key(row: dict) -> tuple[int, str]:
    """(length, id): ``STEP-\\d{3,6}`` then sorts numerically."""
    tc_id = str(row.get("tc_id") or "")
    return (len(tc_id), tc_id)


def explore_verdict(rows: list) -> str:
    """``pass`` when the last turn is ``done(pass)`` and no row failed, else ``""``."""
    ordered = sorted(rows, key=_turn_key)
    if not ordered or _verdict(ordered[-1]) != VERDICT_PASS:
        return ""
    if any(_verdict(row) == VERDICT_FAIL for row in ordered):
        return ""
    return VERDICT_PASS
