"""Mobile session submit: one exploratory turn (split from session_submit)."""

from __future__ import annotations

import asyncio
import dataclasses

from tools.mobile import explore_runner, knowledge_handover
from tools.mobile.session_submit_args import ExploreCall
from tools.mobile.session_submit_commit import _commit_explore_turn
from tools.mobile.session_submit_parse import (
    _arm_explore_guards,
    _parse_explore_submission,
)
from tools.mobile.session_submit_turn import _replay_explore_turn
from tools.mobile.shown_refs import map_refs


# HAND-OVER TO THE TARGET APP (fix round 3, item 1). A multi-app goal
# (App Tester -> the app under test) moves into a second app, and every guard that
# asks "is this the app under test" reads `ctx.package` -- which stayed
# the run's FIRST app for the whole Air run, so five turns on the right
# screen were refused as SCREEN_NOT_THE_APP. `explore_runner.handover`
# accepts the model's `app` only when it is the package the SERVER saw in
# front when it built this turn's packet, and never a system surface.
# Applied BEFORE the replay and carried on the explore state that
# `apply_turn_result` persists, so `context_for` reads it on every later
# call. `clear_app_data` keeps `ctx.home_package`.
def _apply_handover(call: ExploreCall, payload: dict) -> ExploreCall:
    """The call with the explore state moved to the app the model handed over to."""
    explore = call.resolved.get("explore") or {}
    handed = explore_runner.handover(explore, payload)
    if not handed:
        return call
    call.ctx.package = handed
    call.ctx.activity = ""
    resolved = dict(call.resolved)
    resolved["explore"] = dict(explore, app=handed)
    return dataclasses.replace(call, resolved=resolved)


async def _submit_explore(call: ExploreCall) -> dict:
    """One exploratory turn: replay the actions, then fold the reply."""
    payload, parsed, refusal = _parse_explore_submission(
        map_refs(call.raw, call.run_id), call.resolved
    )
    if refusal is not None:
        return refusal
    moved = _apply_handover(call, payload)
    if moved is not call:
        app = (moved.resolved.get("explore") or {}).get("app")
        await asyncio.to_thread(knowledge_handover.follow, call.run_id, str(app))
    call = moved
    _arm_explore_guards(call.run_id, call.ctx, call.resolved)
    replay = await _replay_explore_turn(call, payload, parsed)
    if isinstance(replay, dict):
        return replay
    return await _commit_explore_turn(call, payload, replay)
