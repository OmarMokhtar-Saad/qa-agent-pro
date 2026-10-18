"""Auto-replay: run a saved flow step by step, checking each step landed.

Each step runs alone through the ordinary executor (guards, budgets and the
missing-element stop all apply), then the server waits, polling the screen until its
key equals the one recorded for that step or the timeout passes. On a mismatch, a
failed step or a missing input it stops and says which step and why. Never raises.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from tools.mobile import (
    actions,
    flow_replay_batch,
    flow_replay_dialog,
    flow_replay_match,
    flow_replay_stop,
    media,
)
from tools.mobile.app_flows_consts import OK_OUTCOMES
from tools.mobile.replay.replay import replay as replay_replay
from tools.mobile.replay.waits_poll import _wait_for
from tools.mobile.replay.waits_types import Probe

logger = logging.getLogger(__name__)

#: Longest the server waits for a step's recorded screen to appear.
LANDING_WAIT_MS = 6000

#: Start of the stop reason when a step needs a tester input not yet given.
MISSING_INPUT = "the tester has not supplied"

#: The stop reason when the live screen is not the one a step was saved from.
WRONG_SCREEN = "the screen is not the one this step was saved from"


class Walk(NamedTuple):
    """A flow replay in progress: the live screen, the next step, what ran, why it ended."""

    screen: Any
    pos: int
    trace: list
    why: str = ""


class Plan(NamedTuple):
    """What to replay: the flow template and the tester inputs it may use."""

    template: dict
    inputs: dict
    ref: media.ClipRef


def _at(ctx: Any, screen: Any, wants: tuple) -> bool:
    return bool(set(wants) & set(flow_replay_match.live_keys(screen, ctx)))


def _branch(ctx: Any, known: frozenset, start: Any) -> Any:
    """Condition: the live screen is steady, is not where the wait began, and is a
    screen some saved flow knows (a branch the app took, not a slow load)."""

    def met(previous: Any, current: Any) -> bool:
        if previous is None:
            return False
        own = flow_replay_match.live_key(current, ctx)
        steady = own == flow_replay_match.live_key(previous, ctx)
        moved = own != flow_replay_match.live_key(start, ctx)
        return (
            steady
            and moved
            and bool(set(flow_replay_match.live_keys(current, ctx)) & known)
        )

    return met


async def _land(ctx: Any, screen: Any, wants: tuple) -> tuple:
    """``(landed, screen)``: wait until the live key is one of *wants* (none: landed).

    The wait also ends, not landed, once the screen is steady on a key another saved
    flow knows: the app took a branch, so the replay need not sit out the timeout.
    """
    if not wants or _at(ctx, screen, wants):
        return True, screen
    package = str(getattr(ctx, "package", "") or "")
    branch = _branch(ctx, flow_replay_match.known_keys(package), screen)
    probe = Probe(
        LANDING_WAIT_MS,
        lambda prev, cur: _at(ctx, cur, wants) or branch(prev, cur),
        "waiting for the recorded screen",
    )
    _found, latest, _timed_out = await _wait_for(ctx, screen, probe)
    return _at(ctx, latest, wants), latest


async def _run_batch(ctx: Any, ref: media.ClipRef, steps: list) -> dict:
    """The replay content of *steps* as one script on one clip; ``{}`` if it cannot run."""
    parsed = actions.parse_script({"actions": steps}, max_actions=actions.MAX_ACTIONS)
    if parsed.get("error"):
        return {}
    clip = (await media.start_clip(ref, parsed["content"])).get("content")
    got = await replay_replay(parsed["content"], ctx)
    if got.get("error"):
        await media.abandon(ref.serial, clip)
        return {}
    content = got.get("content") or {}
    shown = str((content.get("screen") or {}).get("screen_id") or "")
    await media.finish_step(ref, clip, shown)
    return content


def _ok(content: dict) -> bool:
    trace = content.get("trace") or []
    last = trace[-1] if trace else {}
    return str(last.get("outcome") or "") in OK_OUTCOMES


def _starts(plan: Plan, origin: int, keys: Any) -> bool:
    """Whether step *origin* may start on *screen*: a landing key, or its own target."""
    want = flow_replay_match.landing_keys(plan.template, origin)
    if not want or set(want) & set(keys):
        return True
    return flow_replay_match.anchor.fits(plan.template, keys, origin)


def _gate(ctx: Any, plan: Plan, batch: flow_replay_batch.Batch) -> Any:
    """The per-step gate: the live screen must be the one the step was saved from.

    The first step is judged on the screen in hand; later ones may wait (fresh dumps)
    for it to land. An unresolved mismatch hands the replay back to the model.
    """

    async def gate(index: int, screen: Any) -> tuple:
        want = flow_replay_match.landing_keys(plan.template, batch.origin[index])
        if index == 0:
            keys = flow_replay_match.live_keys(screen, ctx)
            ok = _starts(plan, batch.origin[0], keys)
            return screen, ("" if ok else WRONG_SCREEN)
        landed, latest = await _land(ctx, screen, want)
        return latest, ("" if landed else WRONG_SCREEN)

    return flow_replay_dialog.guarded(gate, plan.template, batch.origin)


async def _finish(ctx: Any, plan: Plan, walk: Walk) -> Walk:
    """The walk after the last step of a batch ran: left the app, unrecorded, or landed."""
    step = plan.template["steps"][walk.pos - 1]
    cut = flow_replay_stop.cut_why(ctx, walk.screen, step)
    if cut:
        return walk._replace(why=cut)
    meta = plan.template.get("meta") or {}
    after = (meta.get("after") or [""] * walk.pos)[walk.pos - 1]
    landed, screen = await _land(ctx, walk.screen, (after,) if after else ())
    ended = walk.pos >= len(plan.template.get("steps") or [])  # nothing to diverge from
    why = "" if landed or ended else flow_replay_stop.UNRECORDED
    return Walk(screen, walk.pos, walk.trace, why)


def _idle(plan: Plan, walk: Walk, batch: flow_replay_batch.Batch) -> Walk:
    """The walk when nothing in the batch can run: the flow ended, or an input is missing."""
    steps = plan.template["steps"]
    if batch.end >= len(steps):
        return walk._replace(pos=batch.end)
    name = flow_replay_match.needed(steps[batch.end])
    return walk._replace(pos=batch.end, why=MISSING_INPUT + " `%s`" % name)


def _failed_at(content: dict, batch: flow_replay_batch.Batch) -> int:
    """The flow position of the step the batch stopped on, by its script index:
    entries the replay inserts (a dialog tap, a screen check) never shift it."""
    trace = [e for e in content.get("trace") or [] if isinstance(e, dict)]
    marks = [e["index"] for e in trace if isinstance(e.get("index"), int)]
    at = marks[-1] if marks else len(trace) - 1
    return batch.origin[min(max(at, 0), len(batch.origin) - 1)]


async def _batch(ctx: Any, plan: Plan, walk: Walk) -> Walk:
    """Run every step from ``walk.pos`` that can run, as one script."""
    batch = flow_replay_batch.build(plan.template, plan.inputs, walk.pos)
    if not batch.steps:
        return _idle(plan, walk, batch)
    with flow_replay_batch.armed(ctx, _gate(ctx, plan, batch), walk.screen):
        content = await _run_batch(ctx, plan.ref, batch.steps)
    trace = walk.trace + list(content.get("trace") or [])
    screen = content.get("screen") or walk.screen
    if not _ok(content):
        why = flow_replay_stop.failed_why(content)
        return Walk(screen, _failed_at(content, batch), trace, why)
    return await _finish(ctx, plan, Walk(screen, batch.origin[-1] + 1, trace))


async def run(ctx: Any, plan: Plan, walk: Walk) -> Walk:
    """Replay from ``walk.pos`` to the end of the flow or the first thing that stops it."""
    total = len(plan.template.get("steps") or [])
    while walk.pos < total and not walk.why:
        walk = await _batch(ctx, plan, walk)
    return walk
