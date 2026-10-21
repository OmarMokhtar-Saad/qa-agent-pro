"""Replay waits: the baseline of a step is the LAST SCREEN SHOWN TO THE MODEL.

A step used to take as its "before" the screen on the device at step entry. The
model thinks between steps; an app that moves on by itself in that time (a timer,
a load) made the first change-wait start on the NEW screen, wait for a change
away from it and report "the screen did not change" about a change the model
never saw. The screen the model last saw is the run's latest stored screen, so
when the entry screen differs from it the change already happened.
"""

from __future__ import annotations

from tools.mobile import run_store
from tools.mobile.replay.context import Context
from tools.mobile.replay.screen import _screen_hash, _texts
from tools.mobile.replay.waits_prior import note_action


def shown_hash(ctx: Context) -> str:
    """The content hash of the run's latest stored screen, ``""`` when none."""
    run_id = str(getattr(ctx, "run_id", "") or "")
    if not run_id:
        return ""
    return _screen_hash(run_store.latest_screen(run_id))


def note_shown(ctx: Context, entry: object) -> None:
    """Arm the first change-wait as already satisfied when *entry* is not what the model saw."""
    run_id = str(getattr(ctx, "run_id", "") or "")
    screen = run_store.latest_screen(run_id) if run_id else {}
    ctx.shown_texts = _texts(screen) or None
    shown, now = _screen_hash(screen), _screen_hash(entry)
    if shown and now and shown != now:
        note_action(ctx, shown)
