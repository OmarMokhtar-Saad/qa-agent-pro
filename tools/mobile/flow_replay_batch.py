"""Auto-replay as one batch: the steps that can run go through the executor in one call.

The batch starts on the screen already in hand (no dump); a per-step gate, set on the
replay context, checks the live screen against the one each step was saved from, so a
fresh dump happens only on a mismatch. See ``flow_replay_run`` for the gate itself.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, NamedTuple

from tools.mobile import (
    flow_nochange,
    flow_replay_anchor,
    flow_replay_inputs,
    flow_replay_match,
)


class Batch(NamedTuple):
    """The steps of one batch, each one's position in the flow, and where the batch ends."""

    steps: list
    origin: list
    end: int


def build(template: dict, inputs: dict, pos: int) -> Batch:
    """Steps from *pos* up to the first one missing a tester input (no-change steps drop).

    A batch also ends after an anchor wait that optional steps follow, so the replay
    can see whether those steps are still needed (see ``flow_replay_anchor``)."""
    steps: list = []
    origin: list = []
    flow = template["steps"]
    found, inputs = _aliased(template, inputs)
    at = pos
    while at < len(flow):
        if not flow_nochange.skip_at(template, at):
            if _missing(flow[at], inputs):
                break
            steps.append(_ready(flow[at], inputs, found))
            origin.append(at)
            if flow_replay_anchor.ends_batch(flow, at):
                return Batch(steps, origin, at + 1)
        at += 1
    return Batch(steps, origin, at)


def _aliased(template: dict, inputs: dict) -> tuple:
    """``(aliases, inputs)``: the inputs plus each need an input stands in for."""
    found = flow_replay_inputs.aliases(template, inputs)
    return found, {**{n: inputs[k] for n, k in found.items()}, **inputs}


def _ready(step: dict, inputs: dict, found: dict) -> dict:
    return _held_field(flow_replay_match.fill(step, inputs), found)


def _held_field(step: dict, found: dict) -> dict:
    """A secret step aimed at the tester input that stands in for its field.

    The executor types the value held under the step's ``field``; an aliased need
    (flow ``account_number``, tester ``user_ref``) would find none and fail.
    """
    field = str(step.get("field") or "")
    if step.get("secret") and field in found:
        return dict(step, field=found[field])
    return step


def _missing(step: dict, inputs: dict) -> bool:
    name = flow_replay_match.needed(step)
    return bool(name) and name not in inputs


def failed_at(content: dict, batch: Batch) -> int:
    """The flow position of the step the batch stopped on, by its script index:
    entries the replay inserts (a dialog tap, a screen check) never shift it."""
    trace = [e for e in content.get("trace") or [] if isinstance(e, dict)]
    marks = [e["index"] for e in trace if isinstance(e.get("index"), int)]
    at = marks[-1] if marks else len(trace) - 1
    return batch.origin[min(max(at, 0), len(batch.origin) - 1)]


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
