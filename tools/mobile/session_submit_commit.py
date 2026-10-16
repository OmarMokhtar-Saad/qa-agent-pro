"""Mobile session submit: fold, checkpoint and answer a replayed explore turn (split from session_submit)."""

from __future__ import annotations

from tools.mobile import explore_runner, finding_check, media
from tools.mobile.session_explore_case import TurnRecord, _checkpoint_explore_turn
from tools.mobile.session_explore_net import _explore_finish_network
from tools.mobile.session_explore_plan import _explore_planned_case, _persist_explore
from tools.mobile.session_explore_prior import _latest_finding
from tools.mobile.session_packets import _finish_evidence
from tools.mobile.session_submit_args import (
    ExploreCall,
    _ReplayedTurn,
    _Settle,
    _TurnReply,
)
from tools.mobile.session_submit_reply import (
    _committed_explore_state,
    _explore_turn_case,
    _explore_turn_reply,
    _turn_verdict,
)


async def _checkpoint_turn(call: ExploreCall, turn: dict, outcome: dict) -> dict:
    """Stop the capture and checkpoint the replayed turn as a case."""
    before = call.resolved.get("explore") or {}
    finding = _latest_finding(turn.get("state"))
    verdict = _turn_verdict(outcome)
    net = await _explore_finish_network(call.run_id, before, call.ctx)
    record = TurnRecord(before, outcome, finding, verdict, net)
    return _checkpoint_explore_turn(call.run_id, record)


async def _finish_clip(call: ExploreCall, replay: _ReplayedTurn) -> None:
    """File the turn's clip onto the case record the checkpoint created."""
    ref = media.ClipRef(call.run_id, replay.clip_tc_id, call.ctx.serial, True)
    screen_id = str((replay.outcome.get("screen") or {}).get("screen_id") or "")
    await media.finish_step(ref, replay.clip, screen_id)


# Carried on `turn["state"]`, not `turn` itself: `committed_state` is
# built from `turn.get("state")`, and that is the ONLY part of `turn`
# `_persist_explore` writes back into `resolved["explore"]` -- a queue
# attached anywhere else on `turn` would never reach the next submit.
# The turn just replayed, checkpointed as a case. ``resolved["explore"]``
# carries the turn NUMBER this submit answered, so the id is the turn's
# own and is the same on a resubmit.
# ONE producer for this turn's verdict, read by BOTH consumers below --
# the DISK record and the CHAT reply.
# The turn HAS been replayed, so its capture is complete. Stopped before the
# checkpoint, because the checkpoint is what carries the record to the report.
# AFTER the checkpoint, and that ordering is the contract, not a style
# choice. `media.remember` APPENDS onto the case record; in this lane the
# checkpoint above is what CREATES that record, so calling this first would
# find no case, log the loss and drop the clip on the floor.
def _checked(payload: dict, outcome: dict, state: dict) -> dict:
    """*payload* with its finding marked unverified when the trace contradicts it."""
    if not isinstance(payload, dict) or not payload.get("finding"):
        return payload
    finding = finding_check.check_finding(
        payload["finding"], _full_trace(outcome, state)
    )
    return {**payload, "finding": finding}


async def _commit_explore_turn(
    call: ExploreCall, payload: dict, replay: _ReplayedTurn
) -> dict:
    """Fold the replayed turn into the explore state, checkpoint it and reply."""
    outcome = replay.outcome
    state = call.resolved.get("explore") or {}
    folded = explore_runner.apply_turn_result(state, _checked(payload, outcome, state))
    if folded.get("error"):
        return folded
    turn = folded.get("content") or {}
    if isinstance(turn.get("state"), dict):
        turn["state"]["queued_actions"] = outcome.get("queued_actions") or []
    _fold_auto_trace(turn, outcome)
    await media.flush(call.run_id)
    checkpoint = await _checkpoint_turn(call, turn, outcome)
    await _finish_clip(call, replay)
    return await _settle_explore_turn(
        _Settle(call.run_id, call.resolved, turn, outcome, checkpoint)
    )


async def _settle_explore_turn(settle: _Settle) -> dict:
    """Persist the committed explore state, close the run if over, and reply."""
    turn, outcome, resolved = settle.turn, settle.outcome, settle.resolved
    before = resolved.get("explore") or {}
    finding = _latest_finding(turn.get("state"))
    verdict = _turn_verdict(outcome)
    committed_state, refused = _committed_explore_state(turn, outcome)
    planned = _explore_planned_case(settle.checkpoint["tc_id"], before, finding)
    _persist_explore(settle.run_id, committed_state, planned=planned)
    # ONE derivation of "has this run stopped", read by BOTH consumers below.
    stopped = refused or str(turn.get("status") or "") != explore_runner.RUNNING
    if stopped:
        await _finish_evidence(settle.run_id, resolved)
    notice = (
        explore_runner.GUARD_REFUSED_NOTICE
        if refused
        else str(turn.get("notice") or "")
    )
    case = _explore_turn_case(settle.checkpoint, outcome, verdict)
    return _explore_turn_reply(_TurnReply(case, notice, resolved, stopped))


def _full_trace(outcome: dict, state: dict) -> list:
    """This turn's trace with the steps auto-replayed before it in front."""
    carried = state.get("auto_trace") if isinstance(state, dict) else None
    return list(carried or []) + list(outcome.get("trace") or [])


def _fold_auto_trace(turn: dict, outcome: dict) -> None:
    """Put the steps the server auto-replayed before this turn in front of its trace."""
    state = turn.get("state")
    carried = state.pop("auto_trace", None) if isinstance(state, dict) else None
    if carried:
        outcome["trace"] = list(carried) + list(outcome.get("trace") or [])
