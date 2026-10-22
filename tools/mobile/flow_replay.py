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
    flow_replay_fresh,
    flow_replay_match,
    flow_replay_note,
    flow_replay_record,
    flow_replay_run,
    flow_replay_state,
    flow_replay_stop,
    flow_replay_switch,
    flow_replay_visited,
    flow_stats,
    held_inputs,
    kstg_flow_sig,
    media,
    session_explore_ids,
    step_timing,
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
    first_only: bool = False  # host lane: only a flow whose FIRST step fits
    seconds: float = 0.0  # one pass's replay budget (0 = MAX_CHAIN_SECONDS)


def _inputs(run_id: str, ctx: Any) -> dict:
    merged = dict(
        held_inputs.recall(run_id), **(getattr(ctx, "tester_inputs", None) or {})
    )
    ctx.tester_inputs = merged
    return merged


def _same_package(last: object, package: str) -> dict:
    """The earlier replay state, or ``{}`` when it ran in another package (H19: a
    flow chain from one app is never carried into the run's current app)."""
    got = last if isinstance(last, dict) else {}
    named = str(got.get("package") or "")
    return got if not named or not package or named == package else {}


def _resume(body: dict, key: Any, package: str) -> tuple:
    """``(name, template, pos)`` of the replay to continue, else a fresh pick."""
    back = flow_replay_switch.resume(body, key, package)
    if back[1]:
        return back
    last = _same_package(body.get("auto_replay"), package)
    name = str(last.get("name") or "")
    if name and last.get("retry"):
        got = flow_replay_match.template(package, name)
        pos = int(last.get("pos") or 0)
        if got and flow_replay_fresh.same_shape(last, got):
            if flow_replay_match.starts(got, key, pos):
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
        "sig": kstg_flow_sig.of_template(head.template),
        "done": [] if retry else done[-MAX_CHAIN * 2 :],
        "why": walk.why,
        "retry": retry,
        "note": flow_replay_note.text(head, walk.pos, walk.why),
    }
    named = [dict(entry, flow=name) for entry in walk.trace]
    named = flow_replay_record.mark_stop(named, walk.pos, "" if retry else walk.why)
    body["auto_trace"] = (list(body.get("auto_trace") or []) + named)[-MAX_TRACE:]


def _count(package: str, name: str, walk: flow_replay_run.Walk) -> None:
    """Count the replay in the flow's stats (a missing tester input is not a failure)."""
    if not walk.why.startswith(flow_replay_run.MISSING_INPUT):
        flow_stats.record(package, name, not walk.why)


def _found(auto: Auto, ctx: Any, run_id: str) -> tuple:
    """``(package, (name, template, pos))``: the flow to run and the package it lives in."""
    key = flow_replay_match.live_keys(auto.screen, ctx)
    package = str(getattr(ctx, "package", "") or "")
    if not auto.first_only:
        return package, _resume(auto.body, key, package)
    found = flow_replay_visited.pick_first((run_id, ctx, auto.screen), key, auto.body)
    return found[0] or package, found[1:]


def _next(auto: Auto, ctx: Any, run_id: str = "") -> tuple:
    """``(name, template, pos, package)`` to run now; no template when the chain is over.

    The host lane (B13) also looks in the packages this run visited; *package* is
    where the flow was found.
    """
    package, got = _found(auto, ctx, run_id)
    name, template, pos = got
    if (
        template
        and auto.ran
        and flow_replay_stop.inert_tail(template, pos, auto.screen)
    ):
        flow_replay_stop.chain_done(auto.body, name, pos)
        return name, {}, pos, package
    return (*got, package)


def _case(auto: Auto) -> str:
    """The clip case id: the host lane's own, else the explore turn's."""
    return auto.body.get("case_id") or session_explore_ids.explore_turn_case_id(
        auto.body
    )


async def _walk(ctx: Any, auto: Auto, at: tuple) -> flow_replay_run.Walk:
    """The walk of flow template ``at[1]`` from step ``at[2]`` on the live screen."""
    serial = str(getattr(ctx, "serial", "") or "")
    ref = media.ClipRef(at[0], _case(auto), serial)
    plan = flow_replay_run.Plan(at[1], _inputs(at[0], ctx), ref)
    start = flow_replay_run.Walk(auto.screen, at[2], [])
    return await step_timing.timed("replay", flow_replay_run.run(ctx, plan, start))


async def _one(run_id: str, ctx: Any, auto: Auto) -> tuple:
    """``(Auto, stop)``: one flow replayed from the live screen; stop ends the chain."""
    name, template, pos, package = _next(auto, ctx, run_id)
    if not template:
        return auto, True
    walk = await _walk(ctx, auto, (run_id, template, pos))
    live = flow_replay_match.live_key(auto.screen, ctx)
    said = tuple(flow_replay_record.said(walk.trace))
    head = flow_replay_note.Head(name, template, pos, live, said)
    _record(auto.body, head, walk)
    auto.body["auto_replay"]["package"] = package
    _count(package, name, walk)
    detour = flow_replay_switch.detour(auto.body, (name, walk.pos), walk.why)
    return auto._replace(screen=walk.screen, ran=True), bool(walk.why) and not detour


async def run(run_id: str, ctx: Any, auto: Auto) -> Auto:
    """The live screen after every flow that fit and ran (the input when none did)."""
    try:
        if flow_replay_state.honoured(run_id, auto.body.get("goal")):
            return auto
        auto.body.pop("auto_trace", None)
        flow_replay_switch.clear(auto.body)
        if isinstance(auto.body.get("auto_replay"), dict):
            auto.body["auto_replay"]["note"] = ""
        deadline = time.monotonic() + (auto.seconds or MAX_CHAIN_SECONDS)
        for _ in range(MAX_CHAIN):
            auto, stop = await _one(run_id, ctx, auto)
            if stop or time.monotonic() > deadline:
                break
    except Exception:
        logger.exception("flow replay failed")
    flow_replay_switch.clear(auto.body)
    return auto
