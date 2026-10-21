"""Moved from handlers (refactor P2): mobile_step."""

from __future__ import annotations

import logging
from dataclasses import replace

from handlers import _common, mobile_gate
from handlers.mobile.step_acquire import _mobile_step_acquire as mobile_step_acquire
from handlers.mobile.step_audit import (  # noqa: F401
    StepTurn,
    _forget_briefing_if_needs_model,
    _mobile_step_audit,
    _mobile_step_footer,
    _parse_tester_inputs,
)
from handlers.mobile.step_capture import (  # noqa: F401
    ShotTurn,
)
from handlers.mobile.step_memo import (  # noqa: F401
    MemoCtx,
    _mobile_briefing_memo,
    _mobile_screen_memo,
    _mobile_shown_packet,
)
from handlers.mobile.step_next import (  # noqa: F401
    NextOpts,
    NextTurn,
)
from handlers.mobile.step_next import _mobile_next as mobile_next  # noqa: F401
from handlers.mobile.step_notes import (  # noqa: F401
    NoteAsk,
    _call_arg,
    _mobile_call_secrets,
    _mobile_held_values,
    _mobile_note_spec,
    _mobile_note_target,
    mobile_note_intake,
)
from handlers.mobile.step_packet import (  # noqa: F401
    PacketAsk,
    _mobile_packet_header,
    _mobile_packet_text,
)
from handlers.mobile.step_replay import _mobile_step_run as mobile_step_run
from handlers.mobile.step_report import (  # noqa: F401
    _mobile_render_report_file,
    _mobile_report_text,
)
from handlers.mobile.step_resume import (
    _mobile_resume_device_stage as mobile_resume_device_stage,  # noqa: F401
)
from handlers.mobile.step_state import (  # noqa: F401
    _MOBILE_LEARN_TASKS,
    _MOBILE_RUN_ENV,
    _MOBILE_SAME_SCREEN_NOTE,
    _MOBILE_SHOT_WANT,
    _MOBILE_STEP_HELD,
    _MOBILE_TOOL_FAILURES,
    _mark_mobile_step_held,
    _mobile_route_kwargs,
    _note_mobile_step_case,
)
from handlers.mobile.step_support import (
    _mobile_start_heartbeat as mobile_start_heartbeat,  # noqa: F401
)
from handlers.mobile.step_support import (  # noqa: F401
    _mobile_tool_failed,
    _mobile_unresolved,
)
from tools.mobile import step_arm
from tools.mobile.session_core import new_budget
from tools.untrusted import single_line as safe

logger = logging.getLogger("tools.mcp_handlers")


# Replay the actions YOU planned, then hand back the verdict and next packet.
#
# ``tester_input`` is a value the TESTER typed in chat for one named field. It
# is typed to the device through the IME on stdin and goes nowhere else: not
# into the reply, the trace, the checkpoint or the audit row. The audit detail
# is built by ``session.audit_detail``, which has NO parameter that could
# carry a value -- ``audit_log.record_event`` writes ``detail`` verbatim, and
# ``run_store.redact`` alone would not mask a value under a field name the
# tester chose.
#
# tools.untrusted, not agents.api_test_agent -- see handle_mobile_test.
# Note the import precedes the lane check, so an unshipped module here takes
# out the REFUSAL path too, not just the working one.
async def handle_submit_mobile_step(  # noqa: PLR0913 - MCP tool schema
    run_id: str,
    tc_id: str = "",
    script: str = "",
    tester_input: str = "",
    tester_input_field: str = "",
    session_token: str = "",
    *,
    confirm_destructive: bool = False,
    tester_inputs: str = "",
    progress: _common.ProgressCb = None,
) -> str:
    if (off := _lane_off()) is not None:
        return off
    turn = _step_turn(locals())
    try:
        return await _mobile_step_timed(turn)
    except Exception as exc:  # never-raise contract
        logger.exception("mcp submit_mobile_step failed")
        return _step_failed(exc)


def _lane_off():
    # tools.untrusted is imported BEFORE the lane check, so an unshipped module
    # takes out the REFUSAL path too, not just the working one.

    if not _common.mobile_lane_enabled():
        return mobile_gate._mobile_lane_off_message()
    return None


def _step_call(fields: dict) -> dict:
    return {
        "tc_id": fields["tc_id"],
        "script": fields["script"],
        "tester_input": fields["tester_input"],
        "tester_input_field": fields["tester_input_field"],
        "tester_inputs": fields["tester_inputs"],
        "confirm_destructive": fields["confirm_destructive"],
    }


def _step_turn(fields: dict) -> StepTurn:
    # ``locals()`` of the handler also holds ``off``: only named keys are read.
    return StepTurn(
        run_id=fields["run_id"],
        token=fields["session_token"],
        call=_step_call(fields),
        progress=fields["progress"],
    )


def _step_failed(exc: Exception) -> str:

    _common.capture_error(exc, "qa_submit_mobile_step")
    return "⚠️ That step could not be replayed: " + safe(str(exc), 200)


async def _mobile_step_timed(turn: StepTurn) -> str:
    """Start the step budget and timing, acquire the run, then replay."""

    # Created here, so the time the replay itself spends counts against the
    # budget the NEXT packet is produced under.
    budget = new_budget()
    # Fix round 2, item 4: arm the per-phase breakdown for this call.
    step_arm.begin_inner()
    early, token = await mobile_step_acquire(turn.run_id, turn.token)
    if early is not None:
        return early
    return await mobile_step_run(replace(turn, token=token, budget=budget))
