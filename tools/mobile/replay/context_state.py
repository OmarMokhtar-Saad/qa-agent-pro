"""Replay per-run state classes, the trace append, the result payload and the sleep seam."""

from __future__ import annotations

import asyncio
import dataclasses
import time
from typing import Any, NamedTuple

from tools.mobile import (
    knowledge_limits,
    step_timing,
)
from tools.mobile.replay.context_consts import MAX_TRACE
from tools.mobile.replay.context_ctx import Context


class ReplayStop(NamedTuple):
    """The six fields every replay stop reports, in ``_result`` order."""

    status: str
    trace: list
    screen: Any
    verdict: str
    reason: str
    stopped_at: int


class Splice(NamedTuple):
    """Where and what ``_StepCursor.insert`` splices into the run."""

    at: int
    steps: Any
    route_locked: bool
    consume: int = 0


@dataclasses.dataclass(frozen=True)
class _ReplayRun:
    """What one replay carries from step to step: the context, the actions and
    the trace being built. Passed to the step helpers of ``_replay_steps``."""

    ctx: Context
    items: list
    trace: list[dict]


class _StepCursor:
    """Iterates ``(index, action)`` over a list that knowledge plugs may splice
    into mid-run. ``insert`` places steps at the cursor and re-enters there, so
    inserted steps meet the same guards as scripted ones."""

    def __init__(self, items: list) -> None:
        self.items = items
        self.pos = 0
        self.budget = knowledge_limits.PRECONDITION_STEPS * 4

    def __iter__(self) -> "_StepCursor":
        return self

    def __next__(self) -> tuple[int, Any]:
        if self.pos >= len(self.items):
            raise StopIteration
        self.pos += 1
        return self.pos - 1, self.items[self.pos - 1]

    def insert(self, splice: Splice) -> bool:
        """Splice *splice.steps* in at ``splice.at``; ``False`` when nothing goes in."""
        # Splice *steps* before ``index``, replacing the next *consume* scripted
        # steps (a shortcut stands in for them); ``False`` (nothing inserted) when
        # the run is route-locked, the steps are empty or the per-run budget is spent.
        new = [st for st in list(splice.steps or []) if st is not None]
        if splice.route_locked or not new or len(new) > self.budget:
            return False
        self.budget -= len(new)
        self.items[splice.at : splice.at + max(0, splice.consume)] = new
        self.pos = splice.at
        return True


@dataclasses.dataclass(frozen=True)
class _Step:
    """One action with the target it resolved to; built before the guard judges it."""

    op: str
    action: Any
    entry: dict
    element: Any
    scroll_missed: bool


@dataclasses.dataclass
class _ReplayState:
    """What changes from one step to the next: the live screen, the budget
    deadline and the screen content before the last screen-changing action.

    ``baseline_texts`` is ``None`` until one has run, which is the honest answer
    ``new_text`` gives when a script asserts a reply before sending anything."""

    screen: dict | None
    deadline: float
    baseline_texts: set | None = None
    # ``(step index, screen hash)`` of the last optional tap that read the
    # screen and found it unchanged: the next step may rely on that.
    settled: tuple = ()


def _append(trace: list[dict], entry: dict) -> None:
    """Stamp the action's duration and keep the entry. The private ``_started``
    mark never reaches the trace: it is popped here, on every path."""
    started = entry.pop("_started", None)
    if started is not None:
        entry["ms"] = int(max(0.0, time.monotonic() - float(started)) * 1000)
    if len(trace) < MAX_TRACE:
        trace.append(entry)


def _result(stop: ReplayStop, **extra: Any) -> dict:
    payload = {
        "status": str(stop.status),
        "trace": list(stop.trace),
        "screen": stop.screen if isinstance(stop.screen, dict) else None,
        "verdict": str(stop.verdict or ""),
        "reason": str(stop.reason or ""),
        "stopped_at": int(stop.stopped_at),
    }
    payload.update(extra)
    return payload


async def _sleep(seconds: float) -> None:
    """Named so a test can patch it; a real sleep would be the slowest thing here."""
    await step_timing.timed("wait", asyncio.sleep(seconds))
