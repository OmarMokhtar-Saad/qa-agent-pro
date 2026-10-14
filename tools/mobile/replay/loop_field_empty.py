"""Replay engine: a typed field that reads empty is a dead field only on the SAME screen.

An auto-submitting OTP field clears (or disappears) the moment the last digit
is accepted, so "empty after typing" with a NEW screen behind it is the app
moving on, not a field that took nothing. The keyboard cannot see the screen,
so it flags the empty read and this module asks the screen.
"""

from __future__ import annotations

from tools.mobile.replay.context import _ReplayRun
from tools.mobile.replay.dump import _dump
from tools.mobile.replay.loop_settle import Acted

SCREEN_MOVED_NOTE = "The screen changed after typing, so the app may have taken it. "


def _fields(screen: dict) -> int:
    """How many editable fields the screen shows."""
    rows = screen.get("elements") or []
    return sum(1 for r in rows if isinstance(r, dict) and r.get("editable"))


def _moved_on(before: object, after: object) -> bool:
    """Whether the app navigated: another activity or package, or the field is gone.

    A toast or an error line changes the content hash but leaves the field, so
    it is NOT moving on (unknown reads: no).
    """
    if not (isinstance(before, dict) and isinstance(after, dict)):
        return False
    if any(before.get(k) != after.get(k) for k in ("package", "activity")):
        return True
    return bool(_fields(before)) and not _fields(after)


async def reconcile_empty_field(run: _ReplayRun, acted: Acted) -> dict:
    """The outcome to act on: ``unknown`` instead of ``no`` when the screen moved."""
    outcome = acted.outcome
    unknown = outcome.get("if_screen_changed")
    if not (outcome.get("error") and isinstance(unknown, dict)):
        return outcome
    dumped = await _dump(run.ctx)
    if dumped.get("error") or not _moved_on(acted.screen, dumped.get("content")):
        return outcome
    return {**unknown, "error": SCREEN_MOVED_NOTE + str(unknown["error"])}
