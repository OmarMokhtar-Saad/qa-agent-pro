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
from handlers.mobile.step_packet import (
    PacketAsk,
    _mobile_packet_text,
    lead_with_note,
    no_assert_note,
    still_changing_note,
)
from handlers.mobile.step_state import _mobile_route_kwargs
from tools.mobile import render as mobile_render
from tools.mobile import run_store, session_submit, step_timing
from tools.untrusted import single_line as _safe

#: Tail of a reply whose script the schema refused: nothing ran, so no dump.
REFUSED_TAIL = (
    "The script was refused before it ran; the screen is unchanged. "
    "Fix it and call `qa_submit_mobile_step` again."
)


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
    if body.get("refused"):
        return REFUSED_TAIL
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
    return tail + still_changing_note(body) + no_assert_note(body)


async def _step_reply(turn: StepTurn, body: dict, previous_screen_id: str) -> str:
    """The verdict line, the notice and the tail, in that order."""

    run_id, case = turn.run_id, body.get("case") or {}
    # The tail persists the post-step screen (explore_runner._auto_replay), so
    # the verdict is built AFTER it: before, it compared the screen with itself.
    tail = await _step_tail(turn, body)
    line = mobile_render.verdict_line(
        case,
        screen=run_store.latest_screen(run_id),
        previous_id=previous_screen_id,
    )
    notice = str(body.get("notice") or "")
    note = str(body.get("permission_note") or "")
    notes = "".join("\n\n" + n for n in (notice, note) if n)
    return lead_with_note(line + notes + "\n\n", tail)


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
