"""Mobile session: submit and explore submit. Facade over the session_submit_* leaves."""

from __future__ import annotations

import logging

from tools.mobile import held_inputs
from tools.mobile.replay.context import Context
from tools.mobile.session_core import LANE_EXPLORE, STATE_REPORT
from tools.mobile.session_runs import resolve
from tools.mobile.session_submit_args import (  # noqa: F401
    ExploreCall,
    SubmitCall,
    _SuiteTarget,
)
from tools.mobile.session_submit_explore import _submit_explore as submit_explore
from tools.mobile.session_submit_inputs import (  # noqa: F401
    _combine_queued_actions,
    _merge_tester_inputs,
    prepare_submit,
)
from tools.mobile.session_submit_parse import (  # noqa: F401
    _explore_needs_model,
    _noted_outcome,
    _parse_explore_submission,
    _prime_explore_turn,
)
from tools.mobile.session_submit_suite import (  # noqa: F401
    SINGLE_ACTION_NOTICE,
    _single_action_notice,
    _submit_suite,
)
from tools.mobile.session_submit_turn import _replay_explore_turn  # noqa: F401

logger = logging.getLogger("tools.mobile.session")


async def submit(call: SubmitCall) -> dict:
    """Replay one answered packet; return the verdict plus the NEXT packet.

    Tester values go on ``Context.tester_inputs`` only; never returned, logged
    or checkpointed. See :func:`_merge_tester_inputs`.
    """
    try:
        resolved = resolve(call.run_id, call.session_token)
        if resolved.get("error"):
            return resolved
        prepared = prepare_submit(call, resolved)
        if isinstance(prepared, dict):
            return prepared
        return await _submit_lane(call, prepared)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("mobile.session.submit failed")
        return {"error": str(exc), "content": None}


async def _submit_lane(call: SubmitCall, prepared: tuple[Context, dict, str]) -> dict:
    """Route the prepared call to the explore lane or the suite lane."""
    ctx, body, field = prepared
    if str(body.get("lane")) == LANE_EXPLORE:
        explore = ExploreCall(call.run_id, call.script, ctx, body)
        return await _submit_explore_held(explore)
    target = _SuiteTarget(call.run_id, call.tc_id, body, field)
    return await _submit_suite(target, call.script, ctx)


async def _submit_explore_held(call: ExploreCall) -> dict:
    """Submit on the explore lane; drop the held inputs once it reports."""

    explored = await submit_explore(call)
    content = explored.get("content") if isinstance(explored, dict) else None
    if isinstance(content, dict) and content.get("state") == STATE_REPORT:
        held_inputs.forget(call.run_id)
    return explored
