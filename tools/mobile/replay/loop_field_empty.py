"""Replay engine: a typed field that reads empty is unknown, not a dead field.

An auto-submitting OTP field clears (or disappears, or is a non-editable view
the app navigates away from) the moment the last digit is accepted, so "empty
after typing" is ambiguous. The keyboard cannot see the screen, so it flags the
empty read and this module waits, bounded, for the app to move on. Nothing
proved the field dead, so the answer is ``unknown`` either way. When the app
moved on, the keystrokes were accepted and the screen advanced: that is an ok
step with a note, and the script goes on. Only a field that stays put is
``needs_model``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tools.mobile.replay.loop_field_moved import moved_on_by_deadline, moved_on_masked
from tools.mobile.replay.loop_settle import Acted

if TYPE_CHECKING:
    from tools.mobile.replay.context import _ReplayRun

ADVANCED_NOTE = (
    "Typed; the screen advanced, so the read-back is unknown (not a failure)."
)


async def reconcile_empty_field(run: _ReplayRun, acted: Acted) -> dict:
    """The outcome to act on: ``unknown`` instead of ``no`` for an empty field.

    When the app moved on, ``read_screen`` carries the post-action screen that
    proved it, so the settle that follows does not dump again.
    """
    outcome = acted.outcome
    unknown = outcome.get("if_screen_changed")
    if not (outcome.get("error") and isinstance(unknown, dict)):
        return outcome
    probe = moved_on_masked if outcome.get("masked") else moved_on_by_deadline
    moved = await probe(run.ctx, acted.screen)
    if moved.moved:
        reply: dict = {"error": None, "content": {"detail": ADVANCED_NOTE}}
        if moved.screen is not None:
            reply["read_screen"] = moved.screen
        return reply
    return unknown
