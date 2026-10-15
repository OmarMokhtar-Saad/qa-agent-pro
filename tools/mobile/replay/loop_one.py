"""Replay engine: one step of the replay, from knowledge to dispatch. Cut verbatim from tools/mobile/replay/replay.py (C5 w17)."""

from __future__ import annotations

import time
from typing import NamedTuple, cast

from tools.mobile import knowledge_hooks
from tools.mobile.replay.context import _ReplayRun, _ReplayState, _Step, _StepCursor
from tools.mobile.replay.guard import NON_ACTUATING_OPS
from tools.mobile.replay.know_guard import NoteStep
from tools.mobile.replay.knowledge import _apply_knowledge
from tools.mobile.replay.loop_dispatch import _dispatch_op
from tools.mobile.replay.loop_open import _step_prelude
from tools.mobile.replay.steps_assert import Seen
from tools.mobile.replay.steps_done import _ask_tester_step, _done_stop
from tools.mobile.replay.steps_elem import Aim, _resolve_element
from tools.mobile.replay.steps_guard import _guard_stop
from tools.mobile.replay.steps_know import (
    Ahead,
    Found,
    _knowledge_post,
    _knowledge_pre,
    _knowledge_stop,
    _knowledge_target,
)
from tools.mobile.replay.steps_stop import StepAt, _budget_stop, _close
from tools.mobile.replay.waits import _wait_until_text
from tools.mobile.replay.waits_cond import _needs_model_reply
from tools.mobile.replay.waits_types import TextWait


class Turn(NamedTuple):
    """The loop state a step runs in: the cursor over the script and the replay state."""

    cursor: _StepCursor
    state: _ReplayState


class Cur(NamedTuple):
    """The step in hand: what it aims at and where it is."""

    aim: Aim
    at: StepAt


async def _knowledge_before(
    run: _ReplayRun, cursor: _StepCursor, ahead: Ahead
) -> tuple:
    """Saved app notes for one step, BEFORE its target resolves.

    ``(screen, reply, inserted)``. A wait note polls (bounded) and an avoid
    note refuses -- ``reply`` is then the stop; ``inserted`` means a note put
    steps in front of this one and the loop must move to them.
    """
    index, action, entry = ahead.step
    at = StepAt(index, entry, ahead.screen)
    op = str(getattr(action, "op", "") or "")
    note = NoteStep(ahead.step, op, ahead.screen, ahead.deadline)
    screen, note_stop = await _apply_knowledge(run, note)
    if note_stop is not None:
        stop = _knowledge_stop(run, at._replace(screen=screen), note_stop)
        return screen, stop, False
    reply, inserted = await _knowledge_pre(run, cursor, ahead._replace(screen=screen))
    return screen, reply, inserted


async def _target_step(
    run: _ReplayRun, aim: Aim, at: StepAt
) -> tuple[_Step | None, dict | None]:
    """Resolve the step's target, then run the destructive guard (`_guard_stop`).

    ``(step, reply)``; ``reply`` is not None when resolution or the guard stops.
    """
    element, missed, reply = _resolve_element(run, aim, at)
    element, reply = await _knowledge_target(run, at, Found(aim.ref, element, reply))
    if reply is not None:
        return None, reply
    step = _Step(aim.op, aim.ref, at.entry, element, missed)
    if not run.ctx.guard_destructive or step.op in NON_ACTUATING_OPS:
        return step, None
    return step, _guard_stop(run, step, at)


def _terminal_stop(run: _ReplayRun, cur: Cur) -> dict | None:
    """The reply of a ``done`` or ``ask_tester`` step."""
    if cur.aim.op == "done":
        return _done_stop(run, cur.aim.ref, cur.at)
    return _ask_tester_step(run, cur.aim.ref, cur.at)


async def _act(run: _ReplayRun, turn: Turn, step: _Step) -> dict | None:
    """Dispatch a step that passed the guard, then tell knowledge how it went."""
    state = turn.state
    before = state.screen
    seen = Seen(state.screen, state.baseline_texts)
    state.screen, reply = await _dispatch_op(run, step, seen)
    _knowledge_post(run, knowledge_hooks.Post(step.entry, before, state.screen))
    return reply


async def _run_step(run: _ReplayRun, turn: Turn, cur: Cur) -> dict | None:
    """Saved notes, target resolution with the guard, then the dispatch."""
    state = turn.state
    entry = cur.at.entry
    ahead = Ahead((cur.at.position, cur.aim.ref, entry), state.screen, state.deadline)
    state.screen, reply, inserted = await _knowledge_before(run, turn.cursor, ahead)
    if reply is not None or inserted:
        return reply
    step, reply = await _target_step(run, cur.aim, cur.at._replace(screen=state.screen))
    if reply is not None:
        return reply
    return await _act(run, turn, cast("_Step", step))


async def _present_cur(run: _ReplayRun, turn: Turn, cur: Cur) -> Cur | dict | None:
    """``tap_text_if_present``: poll (bounded) for its text. Present gives the
    plain ``tap_text`` step to run; absent records ``ok`` (skipped) and gives ``None``.
    A poll cut short (a crash, a dialog, another app) is NOT absence: it is
    recorded ``interrupted`` and the reply hands the screen back to the model.
    """
    action = cur.aim.ref
    want = TextWait(action.text, int(action.max_s * 1000))
    found, screen, _ = await _wait_until_text(run.ctx, turn.state.screen, want)
    interruption, run.ctx.wait_interruption = run.ctx.wait_interruption, ""
    turn.state.screen = screen
    if found:
        return Cur(Aim("tap_text", action), cur.at._replace(screen=screen))
    if interruption:
        return _interrupted_reply(run, cur.at._replace(screen=screen), interruption)
    cur.at.entry.update(
        outcome="ok", detail="`" + action.text[:80] + "` absent, skipped"
    )
    _close(run, cur.at._replace(screen=screen))
    return None


def _interrupted_reply(run: _ReplayRun, at: StepAt, interruption: str) -> dict:
    """Stop for the model: an optional tap whose poll was interrupted."""
    at.entry.update(outcome="interrupted", detail=interruption)
    _close(run, at)
    return _needs_model_reply(run, at.screen, at.entry)


# NEVER on index 0. A budget that can stop the first action makes no
# progress at all, and each stop costs one of three escapes, so a short
# budget would turn every case into `blocked` without ever touching the
# device.
async def _replay_one(run: _ReplayRun, turn: Turn, item: tuple) -> dict | None:
    """One step of the replay; the reply that ends the script, else ``None``."""
    index, action = item
    if index and time.monotonic() >= turn.state.deadline:
        return _budget_stop(run, index, turn.state.screen)
    entry, reply = _step_prelude(run, turn.state, item)
    if reply is not None:
        return reply
    op = str(getattr(action, "op", "") or "")
    cur = Cur(Aim(op, action), StepAt(index, cast("dict", entry), turn.state.screen))
    if op in ("done", "ask_tester"):
        return _terminal_stop(run, cur)
    if op == "tap_text_if_present":
        present = await _present_cur(run, turn, cur)
        if present is None or isinstance(present, dict):
            return present
        return await _run_step(run, turn, present)
    return await _run_step(run, turn, cur)
