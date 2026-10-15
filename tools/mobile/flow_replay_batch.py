"""Auto-replay as one batch: the steps that can run go through the executor in one call.

The batch starts on the screen already in hand (no dump); a per-step gate, set on the
replay context, checks the live screen against the one each step was saved from, so a
fresh dump happens only on a mismatch. See ``flow_replay_run`` for the gate itself.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, NamedTuple

from tools.mobile import flow_nochange, flow_replay_match


class Batch(NamedTuple):
    """The steps of one batch, each one's position in the flow, and where the batch ends."""

    steps: list
    origin: list
    end: int


def build(template: dict, inputs: dict, pos: int) -> Batch:
    """Steps from *pos* up to the first one missing a tester input (no-change steps drop)."""
    steps: list = []
    origin: list = []
    flow = template["steps"]
    at = pos
    while at < len(flow):
        if not flow_nochange.skip_at(template, at):
            name = flow_replay_match.needed(flow[at])
            if name and name not in inputs:
                break
            steps.append(flow_replay_match.fill(flow[at], inputs))
            origin.append(at)
        at += 1
    return Batch(steps, origin, at)


def wanted(template: dict, pos: int) -> str:
    """The screen key step *pos* was saved from (``""`` when none was recorded)."""
    before = (template.get("meta") or {}).get("before") or []
    return str(before[pos] or "") if pos < len(before) else ""


@contextmanager
def armed(ctx: Any, gate: Any, screen: Any) -> Iterator[None]:
    """Give the replay *ctx* the step *gate* and the start *screen*; restore both after."""
    held = (getattr(ctx, "step_gate", None), getattr(ctx, "screen", None))
    ctx.step_gate, ctx.screen = gate, screen
    try:
        yield
    finally:
        ctx.step_gate, ctx.screen = held
