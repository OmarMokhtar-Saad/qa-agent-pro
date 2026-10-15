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
    flow_nochange,
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


async def _land(ctx: Any, screen: Any, want: str) -> tuple:
    """``(landed, screen)``: wait until the live key is *want* (no key recorded: landed)."""
    if not want or want in flow_replay_match.live_keys(screen, ctx):
        return True, screen
    probe = Probe(
        LANDING_WAIT_MS,
        lambda _p, cur: want in flow_replay_match.live_keys(cur, ctx),
        "waiting for the recorded screen",
    )
    found, latest, _timed_out = await _wait_for(ctx, screen, probe)
    return bool(found), latest


async def _run_one(ctx: Any, ref: media.ClipRef, step: dict) -> dict:
    """The replay content of the one *step*, recorded on the clip *ref* names.

    ``{}`` when the step cannot be built or replayed (the clip is then abandoned).
    """
    parsed = actions.parse_script({"actions": [step]})
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


def _problem(ctx: Any, plan: Plan, walk: Walk) -> str:
    """Why step ``walk.pos`` cannot run now (wrong screen, missing input), else ``""``."""
    meta = plan.template.get("meta") or {}
    want = (meta.get("before") or [""] * (walk.pos + 1))[walk.pos]
    if want and want not in flow_replay_match.live_keys(walk.screen, ctx):
        return "the screen is not the one this step was saved from"
    name = flow_replay_match.needed(plan.template["steps"][walk.pos])
    if name and name not in plan.inputs:
        return MISSING_INPUT + " `%s`" % name
    return ""


async def _after(ctx: Any, plan: Plan, walk: Walk) -> Walk:
    """The walk after step ``walk.pos - 1`` ran: left the app, unrecorded, or landed."""
    pos, screen, trace = walk.pos, walk.screen, walk.trace
    step = plan.template["steps"][pos - 1]
    cut = flow_replay_stop.cut_why(ctx, screen, step)
    if cut:
        return Walk(screen, pos, trace, cut)
    meta = plan.template.get("meta") or {}
    want = (meta.get("after") or [""] * pos)[pos - 1]
    landed, screen = await _land(ctx, screen, want)
    why = "" if landed else flow_replay_stop.UNRECORDED
    return Walk(screen, pos, trace, why)


async def _step(ctx: Any, plan: Plan, walk: Walk) -> Walk:
    """Run step ``walk.pos``; the next walk, with ``why`` set when the replay ends there."""
    if flow_nochange.skip_at(plan.template, walk.pos):
        return walk._replace(pos=walk.pos + 1)
    bad = _problem(ctx, plan, walk)
    if bad:
        return walk._replace(why=bad)
    step = flow_replay_match.fill(plan.template["steps"][walk.pos], plan.inputs)
    content = await _run_one(ctx, plan.ref, step)
    trace = walk.trace + list(content.get("trace") or [])
    screen = content.get("screen") or walk.screen
    if not _ok(content):
        why = flow_replay_stop.failed_why(content)
        return Walk(screen, walk.pos, trace, why)
    return await _after(ctx, plan, Walk(screen, walk.pos + 1, trace))


async def run(ctx: Any, plan: Plan, walk: Walk) -> Walk:
    """Replay from ``walk.pos`` to the end of the flow or the first thing that stops it."""
    total = len(plan.template.get("steps") or [])
    while walk.pos < total and not walk.why:
        walk = await _step(ctx, plan, walk)
    return walk
