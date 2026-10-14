"""Replay one mobile step and build the verdict plus the next packet."""

from __future__ import annotations

from dataclasses import replace

from handlers.mobile.step_audit import (
    StepTurn,
    _mobile_step_audit,
    _mobile_step_footer,
    _parse_tester_inputs,
)
from handlers.mobile.step_hooks import mobile_step
from handlers.mobile.step_packet import PacketAsk, _mobile_packet_text
from handlers.mobile.step_state import _mobile_route_kwargs
from tools.mobile import render as mobile_render
from tools.mobile import run_store, session_submit, step_timing
from tools.untrusted import single_line as _safe


def _submit_call(turn: StepTurn):
    """The ``SubmitCall`` for this step's script, under the step budget."""

    call = turn.call
    return session_submit.SubmitCall(
        turn.run_id,
        call["tc_id"],
        call["script"],
        session_token=turn.token,
        tester_input=call["tester_input"],
        tester_input_field=call["tester_input_field"],
        tester_inputs=turn.parsed_inputs,
        confirm_destructive=call["confirm_destructive"],
        budget=turn.budget,
        **_mobile_route_kwargs(),
    )


async def _replay_submit(turn: StepTurn) -> dict:
    """Run the tester's script through ``session.submit`` under the step budget."""

    return await step_timing.timed("replay", session_submit.submit(_submit_call(turn)))


async def _step_tail(turn: StepTurn, body: dict) -> str:
    """The packet the replay already carries, else the next packet."""
    run_id, token, budget = turn.run_id, turn.token, turn.budget
    progress = turn.progress
    if body.get("packet"):
        tail = await _mobile_packet_text(
            PacketAsk(
                body["packet"], token, resolved=body.get("resolved"), budget=budget
            )
        )
    else:
        tail = await mobile_step.mobile_next(
            run_id, token, mobile_step.NextOpts(progress=progress, budget=budget)
        )
    return tail


async def _step_reply(turn: StepTurn, body: dict, previous_screen_id: str) -> str:
    """The verdict line, the notice and the tail, in that order."""

    run_id, case = turn.run_id, body.get("case") or {}
    line = mobile_render.verdict_line(
        case,
        screen=run_store.latest_screen(run_id),
        previous_id=previous_screen_id,
    )
    notice = str(body.get("notice") or "")
    tail = await _step_tail(turn, body)
    return line + (("\n\n" + notice) if notice else "") + "\n\n" + tail


def _previous_screen_id(run_id: str) -> str:
    """The screen id shown before this step, ``""`` when none."""

    return str((run_store.latest_screen(run_id) or {}).get("screen_id") or "")


async def _mobile_step_run(turn: StepTurn) -> str:
    """Replay the script, audit it, and build the verdict plus the next packet."""
    run_id, call = turn.run_id, turn.call
    tc_id = call["tc_id"]
    parsed = _parse_tester_inputs(call["tester_inputs"])
    previous_screen_id = _previous_screen_id(run_id)
    turn = replace(turn, parsed_inputs=parsed)
    result = await _replay_submit(turn)
    if result.get("error"):
        return "⚠️ " + _safe(result["error"], 400)
    body = result.get("content") or {}
    case = body.get("case") or {}
    await _mobile_step_audit(turn, case)
    reply = await _step_reply(turn, body, previous_screen_id)
    return reply + _mobile_step_footer(run_id, tc_id, body)
