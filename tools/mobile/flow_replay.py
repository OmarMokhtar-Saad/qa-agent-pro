"""Auto-replay in the explore lane: the server drives a learned flow, the model steps in.

Called by ``explore_runner.next_turn`` after the screen is observed. When the screen
matches a step of a saved flow (or the step a replay stopped waiting at), the
flow runs on its own, each step checked against the screen recorded for it. On any
divergence it stops and the model gets the screen it is really on, plus a note saying
which step was reached and why the replay ended. Never raises.
"""

from __future__ import annotations

import logging
import time
from typing import Any, NamedTuple

from tools.mobile import (
    flow_replay_match,
    flow_replay_note,
    flow_replay_run,
    flow_replay_state,
    flow_replay_switch,
    flow_stats,
    held_inputs,
    media,
    session_explore_ids,
)

logger = logging.getLogger(__name__)

#: Flows chained in one turn (packets of one goal follow each other).
MAX_CHAIN = 4
#: Seconds one turn may spend replaying chained flows before the model takes over.
MAX_CHAIN_SECONDS = 120.0
#: Longest trace carried to the next submit.
MAX_TRACE = 40


class Auto(NamedTuple):
    """The live screen and the state body one auto-replay pass works on."""

    screen: Any
    body: dict
    ran: bool


def _inputs(run_id: str, ctx: Any) -> dict:
    merged = dict(
        held_inputs.recall(run_id), **(getattr(ctx, "tester_inputs", None) or {})
    )
    ctx.tester_inputs = merged
    return merged


def _resume(body: dict, key: str, package: str) -> tuple:
    """``(name, template, pos)`` of the replay to continue, else a fresh pick."""
    back = flow_replay_switch.resume(body, key, package)
    if back[1]:
        return back
    last = body.get("auto_replay") or {}
    name = str(last.get("name") or "")
    if name and last.get("retry"):
        got = flow_replay_match.template(package, name)
        pos = int(last.get("pos") or 0)
        if got and flow_replay_match.starts(got, key, pos):
            return name, got, pos
    return flow_replay_match.pick(package, key, last.get("done") or ())


def _record(
    body: dict, head: flow_replay_note.Head, walk: flow_replay_run.Walk
) -> None:
    name = head.name
    done = list((body.get("auto_replay") or {}).get("done") or []) + [name]
    retry = walk.why.startswith(flow_replay_run.MISSING_INPUT)
    body["auto_replay"] = {
        "name": name,
        "pos": walk.pos,
        "start": head.start,
        "total": len(head.template.get("steps") or []),
        "done": [] if retry else done[-MAX_CHAIN * 2 :],
        "why": walk.why,
        "retry": retry,
        "note": flow_replay_note.text(head, walk.pos, walk.why),
    }
    body["auto_trace"] = (list(body.get("auto_trace") or []) + walk.trace)[-MAX_TRACE:]


def _count(package: str, name: str, walk: flow_replay_run.Walk) -> None:
    """Count the replay in the flow's stats (a missing tester input is not a failure)."""
    if not walk.why.startswith(flow_replay_run.MISSING_INPUT):
        flow_stats.record(package, name, not walk.why)


async def _one(run_id: str, ctx: Any, auto: Auto) -> tuple:
    """``(Auto, stop)``: one flow replayed from the live screen; stop ends the chain."""
    key = flow_replay_match.live_keys(auto.screen, ctx)
    name, template, pos = _resume(
        auto.body, key, str(getattr(ctx, "package", "") or "")
    )
    if not template:
        return auto, True
    case = session_explore_ids.explore_turn_case_id(auto.body)
    ref = media.ClipRef(run_id, case, str(getattr(ctx, "serial", "") or ""))
    plan = flow_replay_run.Plan(template, _inputs(run_id, ctx), ref)
    walk = await flow_replay_run.run(
        ctx, plan, flow_replay_run.Walk(auto.screen, pos, [])
    )
    live = flow_replay_match.live_key(auto.screen, ctx)
    _record(auto.body, flow_replay_note.Head(name, template, pos, live), walk)
    _count(str(getattr(ctx, "package", "") or ""), name, walk)
    detour = flow_replay_switch.detour(auto.body, (name, walk.pos), walk.why)
    return Auto(walk.screen, auto.body, True), bool(walk.why) and not detour


async def run(run_id: str, ctx: Any, auto: Auto) -> Auto:
    """The live screen after every flow that fit and ran (the input when none did)."""
    try:
        if flow_replay_state.opted_out(run_id):
            return auto
        auto.body.pop("auto_trace", None)
        flow_replay_switch.clear(auto.body)
        if isinstance(auto.body.get("auto_replay"), dict):
            auto.body["auto_replay"]["note"] = ""
        deadline = time.monotonic() + MAX_CHAIN_SECONDS
        for _ in range(MAX_CHAIN):
            auto, stop = await _one(run_id, ctx, auto)
            if stop or time.monotonic() > deadline:
                break
    except Exception:
        logger.exception("flow replay failed")
    flow_replay_switch.clear(auto.body)
    return auto
