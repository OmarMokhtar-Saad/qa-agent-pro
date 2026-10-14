"""Replay knowledge: applying an app's saved notes to one action."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from tools.mobile.replay.context import _ReplayRun
from tools.mobile.replay.know_guard import (
    Fired,
    NoteStep,
    _fired_note,
    _guard_wait_budget,
    _knowledge_refusal,
    _live_guards,
    _matched_notes,
)
from tools.mobile.replay.know_wait import WaitJob, _run_wait_guard, _tally_wait

logger = logging.getLogger("tools.mobile.executor")


@dataclass
class _Waited:
    """What the wait notes produced so far (kept if a later note raises)."""

    step: NoteStep
    screen: Any
    fired: list = field(default_factory=list)


async def _wait_notes(run: _ReplayRun, live: list, acc: _Waited) -> None:
    """Poll each live ``wait`` note into *acc*; a timeout proceeds."""
    step = acc.step
    for guard in live:
        if guard.get("kind") != "wait":
            continue
        budget = _guard_wait_budget(run, guard, step)
        if budget <= 0:
            continue
        acc.screen, outcome, spent = await _run_wait_guard(
            run.ctx, WaitJob(guard, acc.screen, budget)
        )
        result = Fired(outcome, spent, step.op)
        _tally_wait(run.ctx, guard, result)
        acc.fired.append(_fired_note(guard, "wait", result))


# Apply this app's saved notes to ONE action, BEFORE it resolves a target.
#
# ``(screen, refusal)``. An ``avoid`` note that matches refuses the op (the
# reply names the note and echoes it as untrusted text). A ``wait`` note polls
# for what it names within a budget that can never hang the replay: the
# smallest of the note's ms, ``MAX_WAIT_MS``, what the script's waits leave of
# ``MAX_TOTAL_WAIT_MS`` after the guard waits already spent, and the time left
# to the replay deadline. A timeout PROCEEDS and is buffered as
# ``contradicted``; an interruption proceeds too, logged as ``interrupted``
# and tallied neither way. This decides nothing about the destructive guard,
# which still runs after and outranks it. Never raises.
async def _apply_knowledge(run: _ReplayRun, step: NoteStep) -> tuple:
    """Apply this app's saved notes to ONE action: ``(screen, refusal)``."""
    ctx, entry = run.ctx, step.step_at[2]
    acc = _Waited(step, step.screen)
    try:
        matched = _matched_notes(ctx, step)
        if not matched:
            return acc.screen, None
        live = await _live_guards(ctx, matched, step.deadline)
        refusal = _knowledge_refusal(ctx, live, step)
        if refusal is not None:
            return acc.screen, refusal
        await _wait_notes(run, live, acc)
        if acc.fired:
            entry["knowledge"] = acc.fired
        return acc.screen, None
    except Exception:
        logger.exception("mobile app knowledge: guard failed; continuing without notes")
        return acc.screen, None
