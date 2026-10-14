"""Replay engine: a typed field that reads empty is unknown, not a dead field.

An auto-submitting OTP field clears (or disappears, or is a non-editable view
the app navigates away from) the moment the last digit is accepted, so "empty
after typing" is ambiguous. The keyboard cannot see the screen, so it flags the
empty read and this module waits, bounded, for the app to move on. Nothing
proved the field dead, so the answer is ``unknown`` either way; moving on only
adds a note.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tools.mobile.replay.loop_field_moved import moved_on_by_deadline
from tools.mobile.replay.loop_settle import Acted

if TYPE_CHECKING:
    from tools.mobile.replay.context import _ReplayRun

SCREEN_MOVED_NOTE = "The screen changed after typing, so the app may have taken it. "


async def reconcile_empty_field(run: _ReplayRun, acted: Acted) -> dict:
    """The outcome to act on: ``unknown`` instead of ``no`` for an empty field."""
    outcome = acted.outcome
    unknown = outcome.get("if_screen_changed")
    if not (outcome.get("error") and isinstance(unknown, dict)):
        return outcome
    if await moved_on_by_deadline(run.ctx, acted.screen):
        return {**unknown, "error": SCREEN_MOVED_NOTE + str(unknown["error"])}
    return unknown
