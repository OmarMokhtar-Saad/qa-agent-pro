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
    "Typed, but the field read EMPTY afterwards (not confirmed as landed; "
    "read-back is unknown); the screen then advanced, so the app may have "
    "taken it and submitted itself. Counted as unconfirmed, not verified."
)


MASKED_NOTE = (
    "Typed into a masked field and could not read it back (read-back is "
    "unknown); the screen then advanced, which is the only proof it was "
    "taken. Counted as unconfirmed, not verified."
)


def _note(outcome: dict) -> str:
    """The detail for an unconfirmed type that advanced the screen."""
    return MASKED_NOTE if outcome.get("masked") else ADVANCED_NOTE


def _advanced_reply(outcome: dict, screen: object) -> dict:
    """The success reply for an unconfirmed type whose screen moved on."""
    reply: dict = {"error": None, "content": {"detail": _note(outcome)}}
    if screen is not None:
        reply["read_screen"] = screen
    return reply


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
        return _advanced_reply(outcome, moved.screen)
    return unknown
