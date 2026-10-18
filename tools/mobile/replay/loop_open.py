"""Replay engine: the first screen, the script's feedback and each step's entry. Cut verbatim from tools/mobile/replay/replay.py (C5 w17)."""

from __future__ import annotations

import logging
import time
from typing import Any, NamedTuple

from tools.mobile import actions as actions_mod
from tools.mobile import knowledge_feedback
from tools.mobile.replay import steps_snapshot
from tools.mobile.replay.context import (
    STATUS_ERROR,
    Context,
    ReplayStop,
    _ReplayRun,
    _ReplayState,
    _result,
)
from tools.mobile.replay.dump import _first_screen
from tools.mobile.replay.screen import (
    EntrySpec,
    _entry,
    _screen_hash,
    _screen_id,
    _texts,
)
from tools.mobile.replay.shown_baseline import note_shown
from tools.mobile.replay.steps_stop import (
    StepAt,
    _reply,
    _route_mismatch_stop,
    _stamp_screen_key,
)
from tools.mobile.replay.verdicts import _route_expected

logger = logging.getLogger("tools.mobile.executor")


class Start(NamedTuple):
    """Where a replay starts: the screen in hand and the trace it appends to."""

    screen: dict | None
    trace: list[dict]


def _record_script_feedback(ctx: Context, script: Any, screen: Any) -> None:
    """``Script.knowledge_feedback`` into ``ctx.kbuf`` (in memory, step index 0).
    Never raises."""
    items = list(getattr(script, "knowledge_feedback", None) or [])
    if not items:
        return
    try:
        knowledge_feedback.record(ctx, items, knowledge_feedback.Anchor(screen, 0))
    except Exception:
        logger.exception("mobile app knowledge: script feedback failed")


async def _replay_open(
    ctx: Context, script: Any, start: Start
) -> tuple[Any, dict | None]:
    """``_replay_first_screen``, then the script's feedback is recorded before any
    step can stop the run, so it is kept even when no step is dispatched."""
    items = list(getattr(script, "actions", None) or [])
    screen, reply = await _replay_first_screen(ctx, items, start)
    _record_script_feedback(ctx, script, screen)
    return screen, reply


async def _replay_first_screen(
    ctx: Context, items: list, start: Start
) -> tuple[dict | None, dict | None]:
    """The screen the first action sees, or the reply that ends the replay
    before it starts: ``(screen, reply)``, ``reply`` is ``None`` to go on."""
    screen, trace = start
    if not items:
        note = "The script had no actions."
        stop = ReplayStop(STATUS_ERROR, trace, screen, "", note, -1)
        return screen, _reply(_result(stop))
    if screen is None:
        dumped = await _first_screen(ctx)
        if dumped.get("error"):
            stop = ReplayStop(STATUS_ERROR, trace, None, "", str(dumped["error"]), -1)
            return screen, _reply(_result(stop))
        screen = dumped.get("content")
    note_shown(ctx, screen)
    return screen, None


def _before_texts(op: str, screen: Any, previous: set | None) -> set | None:
    """The BEFORE set for ``assert new_text``.

    Captured for every op that can change the screen, BEFORE it acts. A wait
    does not move it once an action set it: the screen a wait starts on is
    already the result of the step before it (an OTP that submitted itself),
    so re-baselining there made a reply that had arrived read as "no new
    text". Only a script that starts on a wait takes its baseline there.
    """
    if op not in actions_mod.MUTATING_OPS:
        return previous
    if op in actions_mod.WAIT_OPS and previous is not None:
        return previous
    return _texts(screen)


def _fresh_entry(index: int, action: Any, screen: Any) -> dict:
    """The trace entry for step *index*, stamped with the screen it starts on."""
    started = time.monotonic()
    return _entry(
        EntrySpec(index, action, _screen_id(screen), started, _screen_hash(screen))
    )


# Checks the saved route (S10, PROVISIONAL): the live screen must be the one
# this step was saved from, BEFORE the guard and before anything is actuated.
# Then captures the BEFORE set for ``assert new_text`` for every op that can
# change the screen -- ``wait`` included, because the reply a case is waiting
# for arrives during the wait.
def _step_prelude(
    run: _ReplayRun, state: _ReplayState, item: tuple
) -> tuple[dict | None, dict | None]:
    """The entry for step *index*, or the reply that stops the script before it."""
    index, action = item
    screen = state.screen
    entry = _fresh_entry(index, action, screen)
    _stamp_screen_key(run.ctx, entry, screen)
    steps_snapshot.stamp_target(entry, entry.get("action") or {}, screen)
    op = str(getattr(action, "op", "") or "")
    expected_id = _route_expected(run.ctx, index, len(run.items))
    if expected_id and expected_id != _screen_id(screen):
        return entry, _route_mismatch_stop(run, StepAt(index, entry, screen))
    state.baseline_texts = _before_texts(op, screen, state.baseline_texts)
    return entry, None
