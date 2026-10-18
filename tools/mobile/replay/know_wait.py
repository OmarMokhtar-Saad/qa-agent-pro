"""Replay knowledge: polling for what a ``wait`` note names."""

from __future__ import annotations

import time
from typing import Any, NamedTuple

from tools.mobile.replay.context import Context
from tools.mobile.replay.know_guard import Fired
from tools.mobile.replay.know_read import _known
from tools.mobile.replay.screen import _screen_has, _screen_has_element, absent_from
from tools.mobile.replay.waits_poll import _wait_until_element
from tools.mobile.replay.waits_text import _wait_until_text
from tools.mobile.replay.waits_types import ElementWait, TextWait


class WaitJob(NamedTuple):
    """One wait note to poll: the guard, the screen in hand and the ms budget."""

    guard: dict
    screen: Any
    budget: int


def _already_met(screen: Any, then: dict) -> bool:
    """True when the screen already shows what the note waits for."""
    if then.get("until_rid"):
        has = _screen_has_element(
            screen, str(then["until_rid"]), str(then.get("until_rid_text") or "")
        )
        return absent_from(screen, has) if then.get("until_gone") else has
    return _screen_has(screen, str(then.get("until_text") or ""))


async def _await_condition(ctx: Context, job: WaitJob, then: dict) -> tuple:
    """Poll for the note's condition: ``(satisfied, screen)``."""
    if then.get("until_rid"):
        want = ElementWait(
            str(then["until_rid"]),
            str(then.get("until_rid_text") or ""),
            bool(then.get("until_gone")),
            job.budget,
        )
        satisfied, screen, _late = await _wait_until_element(ctx, job.screen, want)
    else:
        text = TextWait(str(then.get("until_text") or ""), job.budget)
        satisfied, screen, _late = await _wait_until_text(ctx, job.screen, text)
    return satisfied, screen


async def _run_wait_guard(ctx: Context, job: WaitJob) -> tuple:
    """Poll for what a ``wait`` note names: ``(screen, outcome, spent_ms)``."""
    then = job.guard.get("then") or {}
    began = time.monotonic()
    ctx.wait_interruption = ""
    if _already_met(job.screen, then):
        # The screen already shows what the note waits for: nothing was
        # waited on, so it neither confirms nor contradicts the note.
        return job.screen, "already_met", 0
    satisfied, screen = await _await_condition(ctx, job, then)
    spent = int(max(0.0, time.monotonic() - began) * 1000)
    know = _known(ctx)
    know["spent_ms"] = int(know.get("spent_ms") or 0) + spent
    interrupted, ctx.wait_interruption = ctx.wait_interruption, ""
    if satisfied:
        return screen, "confirmed", spent
    return screen, "interrupted" if interrupted else "contradicted", spent


def _tally_wait(ctx: Context, guard: dict, fired: Fired) -> None:
    """Log a wait outcome; an interruption says nothing about the note, so it
    or an already-met condition is logged but tallied neither way."""
    know = _known(ctx)
    if fired.outcome not in ("interrupted", "already_met"):
        tally = know["counts"].setdefault(
            guard.get("id"), {"confirmed": 0, "contradicted": 0}
        )
        tally[fired.outcome] += 1
    know["events"].append(
        {"note_id": guard.get("id"), "event": fired.outcome, "detail": fired.op}
    )
