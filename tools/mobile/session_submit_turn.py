"""Mobile session submit: the recorded replay of one explore turn (split from session_submit)."""

from __future__ import annotations

from tools.mobile import media
from tools.mobile.replay.replay import replay as replay_replay
from tools.mobile.session_explore_ids import explore_turn_case_id
from tools.mobile.session_explore_net import _explore_abort_network
from tools.mobile.session_explore_plan import _persist_explore
from tools.mobile.session_submit_args import ExploreCall, _ReplayedTurn
from tools.mobile.session_submit_parse import _noted_outcome, _prime_explore_turn


# The turn is over and no checkpoint will be written for it, so the
# capture this turn started has no later reader. Stop it here or the
# console records the tester's device until something displaces it.
# The RECORDER is the same shape of leak, one artifact over: no
# checkpoint means no case to file a record onto, so the clip is
# stopped and deleted rather than stored -- `case_runner` calls
# `abandon` on its own error path for exactly this reason.
async def _abort_replay(call: ExploreCall, clip: object) -> None:
    """Abandon the clip and stop the network capture of a turn that errored."""
    await media.abandon(call.ctx.serial, clip)
    state = call.resolved.get("explore") or {}
    _persist_explore(
        call.run_id, await _explore_abort_network(call.run_id, state, call.ctx)
    )


# THE RECORDER, on the lane that shipped without one. `case_runner` has
# started a clip around ITS replay since v1.87.0; this is the other replay
# site and it had none, so every exploratory run -- which is the lane the
# testers actually use -- produced a report with no video while the feature
# read as shipped. Measured on run mrun-20260910-180207-2ee014: five cases,
# zero media records, nothing failed. One question, two sites, one wired.
#
# The id comes from `explore_turn_case_id`, the same producer the checkpoint
# writes the case under.
async def _replay_explore_turn(
    call: ExploreCall, payload: dict, parsed: dict
) -> _ReplayedTurn | dict:
    """Start the clip and replay the actions; a replay error is returned as is."""
    clip_tc_id = explore_turn_case_id(call.resolved.get("explore"))
    ref = media.ClipRef(call.run_id, clip_tc_id, call.ctx.serial)
    clip = (await media.start_clip(ref, parsed["content"])).get("content")
    _prime_explore_turn(call.run_id, call.ctx, payload)
    replayed = await replay_replay(parsed["content"], call.ctx)
    if replayed.get("error"):
        await _abort_replay(call, clip)
        return replayed
    return _ReplayedTurn(_noted_outcome(call.run_id, replayed), clip, clip_tc_id)
