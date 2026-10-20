"""Replay knowledge: which saved notes match an action, and the avoid refusal."""

from __future__ import annotations

import time
from typing import Any, NamedTuple

from tools.mobile import actions as actions_mod
from tools.mobile import app_knowledge, appk_consts
from tools.mobile.replay import dump_activity
from tools.mobile.replay.context import Context, _ReplayRun
from tools.mobile.replay.dump import _focus_probe
from tools.mobile.replay.guard import NON_ACTUATING_OPS
from tools.mobile.replay.know_read import _known
from tools.mobile.replay.know_rids import _action_rids


class NoteStep(NamedTuple):
    """One action the notes apply to: ``(index, action, entry)``, op, screen, deadline."""

    step_at: tuple
    op: str
    screen: Any
    deadline: float


class Fired(NamedTuple):
    """What a wait note did: its outcome, the ms it spent and the op."""

    outcome: str
    spent: int
    op: str


def _matched_notes(ctx: Context, step: NoteStep) -> list:
    """The saved guard notes that match this action, or ``[]`` when none apply."""
    know = getattr(ctx, "knowledge", None)
    if step.op == "wait" or not isinstance(know, dict) or not know.get("guards"):
        return []
    rids = _action_rids(step.step_at[1], step.screen)
    target = app_knowledge.GuardTarget(step.op, rids)
    return app_knowledge.candidates(know["guards"], target)


def _knowledge_refusal(ctx: Context, live: list, step: NoteStep) -> str | None:
    """The avoid-note refusal for the op, or ``None``."""
    # An avoid note refuses an ACTION. A read-only assert or a poll wait
    # changes nothing, so it is never refused.
    if step.op in NON_ACTUATING_OPS or step.op in actions_mod.WAIT_OPS:
        return None
    return _avoid_refusal(ctx, live, step)


def _fired_note(guard: dict, kind: str, fired: Fired) -> dict:
    return {
        "note_id": guard.get("id"),
        "kind": kind,
        "outcome": fired.outcome,
        "ms": fired.spent,
    }


async def _guard_focus(serial: str, budget: float) -> str:
    """The focus an activity-scoped note is judged on; a tree device needs no adb."""
    if dump_activity.tree_path(serial):
        return dump_activity.hint(serial)
    return await _focus_probe(serial, budget)


async def _live_guards(ctx: Context, matched: list, deadline: float) -> list:
    """The matched guards whose activity condition holds on the live focus."""
    know = _known(ctx)
    probe = ""
    if any((g.get("when") or {}).get("activity") for g in matched):
        began = time.monotonic()
        probe = await _guard_focus(ctx.serial, min(2.0, max(0.0, deadline - began)))
        know["spent_ms"] = int(know.get("spent_ms") or 0) + int(
            max(0.0, time.monotonic() - began) * 1000
        )
    return [g for g in matched if app_knowledge.activity_ok(g, probe)]


def _avoid_text(guard: dict) -> str:
    """The refusal sentence for an ``avoid`` note, echoing it as untrusted text."""
    return (
        "A saved note for this app says not to do this here (note #%s). "
        "Do something else, skip this step or mark it blocked, or ask the "
        "tester. If the note is wrong, retire it with "
        'qa_mobile_notes(action="retire", note_id=%s). %s'
        % (guard.get("id"), guard.get("id"), app_knowledge.echo(guard))
    )


def _avoid_refusal(ctx: Context, live: list, step: NoteStep) -> str | None:
    """The refusal text for the first live ``avoid`` note, logging it on the entry."""
    for guard in live:
        if guard.get("kind") != "avoid":
            continue
        _known(ctx)["events"].append(
            {"note_id": guard.get("id"), "event": "avoided", "detail": step.op}
        )
        step.step_at[2]["knowledge"] = [
            _fired_note(guard, "avoid", Fired("avoided", 0, step.op))
        ]
        return _avoid_text(guard)
    return None


def _guard_wait_budget(run: _ReplayRun, guard: dict, step: NoteStep) -> int:
    """Ms a ``wait`` note may poll: the smallest of every cap that applies."""
    then = guard.get("then") or {}
    return min(
        int(then.get("ms") or appk_consts.DEFAULT_GUARD_WAIT_MS),
        actions_mod.MAX_WAIT_MS,
        actions_mod.MAX_TOTAL_WAIT_MS
        - actions_mod.total_wait_ms(run.items)
        - int(_known(run.ctx).get("spent_ms") or 0),
        int((step.deadline - time.monotonic()) * 1000),
    )
