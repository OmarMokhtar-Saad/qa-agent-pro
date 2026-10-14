"""The step handler that carries flow, route, finding and note arguments."""

from __future__ import annotations

import asyncio
from typing import Callable, NamedTuple

from handlers import _common
from handlers.mobile import step as mobile_step
from handlers.mobile.content_args import (
    _FLOW_WARN,
    FlowSpec,
    StepCall,
    _pop_flow_route_args,
    _with_script,
)
from handlers.mobile.content_finding import _mobile_fold_finding
from handlers.mobile.content_flow import _mobile_flow_finish, _mobile_flow_prepare
from handlers.mobile.content_route import _mobile_route_finish, _mobile_route_prepare


class _NoteCtx(NamedTuple):
    """The note of one step call and the run's held values from before the step."""

    note: object
    earlier: list


def _call_secrets(call: StepCall) -> list:
    """The tester-supplied secret values of this step call."""
    step = mobile_step.handle_submit_mobile_step
    return mobile_step._mobile_call_secrets(
        mobile_step._call_arg(step, call, "tester_input"),
        mobile_step._call_arg(step, call, "tester_inputs"),
    )


# The line a ``note`` adds to the step reply: saved, refused, or empty.
async def _mobile_note_line(held: dict, ctx: _NoteCtx, call: StepCall) -> str:
    """The line a ``note`` adds to the step reply: saved, refused, or empty."""
    earlier = ctx.earlier
    if held["held"]:
        run_id = mobile_step._call_arg(
            mobile_step.handle_submit_mobile_step, call, "run_id"
        )
        ask = mobile_step.NoteAsk(
            ctx.note, run_id=run_id, secrets=earlier + _call_secrets(call)
        )
        return await asyncio.to_thread(mobile_step.mobile_note_intake, ask)
    if str(ctx.note or "").strip() and _common.mobile_lane_enabled():
        return "Note not saved: this call did not run a step.\n\n"
    return ""


# The route line then the flow line, each empty when its plan is.
async def _mobile_plan_lines(flow_plan: dict, route_plan: dict, held: dict) -> str:
    """The route line then the flow line, each empty when its plan is."""
    flow_line = ""
    if flow_plan:
        flow_line = await asyncio.to_thread(_mobile_flow_finish, flow_plan, held)
    route_line = ""
    if route_plan:
        route_line = await asyncio.to_thread(_mobile_route_finish, route_plan, held)
    return route_line + flow_line


# Run one flow/route *prepare* for ``spec = (name, params, save_name)``.
# Returns ``(plan, args, kwargs)``: (here ``(plan, call)``) ``{}`` when neither name was
# given, the refusal plan untouched, else the plan with its script spliced into the call.
async def _mobile_plan_one(
    prepare: Callable, call: StepCall, spec: FlowSpec
) -> tuple[dict, StepCall]:
    """Run one flow/route *prepare* for *spec*; ``(plan, call)``."""
    if not (spec.name or spec.save_name):
        return {}, call
    plan = await asyncio.to_thread(prepare, call, spec)
    if plan.get("refusal"):
        return plan, call
    return plan, StepCall(*_with_script(call.args, call.kwargs, plan["script"]))


# Plan the flow, then the route; ``refusal`` is the reply tuple of the first plan that
# refused (the route is not planned after a flow refusal).
async def _mobile_plan_flow_route(
    call: StepCall, flow: FlowSpec, route: FlowSpec
) -> tuple[dict, dict, StepCall, tuple | None]:
    """Plan the flow, then the route; the last item is the refusal reply or None."""
    flow_plan, call = await _mobile_plan_one(_mobile_flow_prepare, call, flow)
    if flow_plan.get("refusal"):
        return flow_plan, {}, call, (flow_plan["refusal"], [])
    route_plan, call = await _mobile_plan_one(_mobile_route_prepare, call, route)
    if route_plan.get("refusal"):
        return flow_plan, route_plan, call, (route_plan["refusal"], [])
    return flow_plan, route_plan, call, None


def _arm_step(kwargs: dict, route_plan: dict) -> tuple[dict, tuple]:
    """Open the screenshot sink, image specs and held marker; ``(held, tokens)``."""
    want = mobile_step._MOBILE_SHOT_WANT.set(
        {"asked": bool(kwargs.pop("screenshot", False))}
    )
    token = mobile_step._MOBILE_IMAGE_SPECS.set([])
    held = {"held": False, "route_expect": list(route_plan.get("expect") or [])}
    held_token = mobile_step._MOBILE_STEP_HELD.set(held)
    return held, (want, token, held_token)


def _disarm_step(tokens: tuple) -> None:
    """Close what ``_arm_step`` opened, in reverse order."""
    want, token, held_token = tokens
    mobile_step._MOBILE_STEP_HELD.reset(held_token)
    mobile_step._MOBILE_IMAGE_SPECS.reset(token)
    mobile_step._MOBILE_SHOT_WANT.reset(want)


async def _run_step(call: StepCall, plans: tuple, held: dict) -> tuple:
    """Run the step, then add the plan and note lines; ``(text, image_specs)``."""
    args, kwargs = call
    note = kwargs.pop("note", "")
    kwargs.pop("env", None)
    run_id = mobile_step._call_arg(
        mobile_step.handle_submit_mobile_step, call, "run_id"
    )
    # Copied BEFORE the step: a step that reaches the report forgets the
    # run's held values, and the note must still be checked against them.
    earlier = mobile_step._mobile_held_values(run_id) if str(note or "").strip() else []
    body = await mobile_step.handle_submit_mobile_step(*args, **kwargs)
    note_line = await _mobile_note_line(held, _NoteCtx(note, earlier), call)
    plan_lines = await _mobile_plan_lines(plans[0], plans[1], held)
    return plan_lines + note_line + body, list(
        mobile_step._MOBILE_IMAGE_SPECS.get() or []
    )


async def _planned(args: tuple, kwargs: dict) -> tuple:
    """Plan flow/route and fold the finding; ``(early, plans, call, finding_note)``."""
    flow_name, flow_params, save_flow, route_name, save_route, finding = (
        _pop_flow_route_args(kwargs)
    )
    if (route_name or save_route) and (flow_name or save_flow):
        refusal = _FLOW_WARN + "Pass a flow OR a route, not both. Nothing was touched."
        return (refusal, []), None, None, ""
    flow_plan, route_plan, call, early = await _mobile_plan_flow_route(
        StepCall(args, kwargs),
        FlowSpec(flow_name, flow_params, save_flow),
        FlowSpec(route_name, flow_params, save_route),
    )
    if early is not None:
        return early, None, None, ""
    args, kwargs, finding_note = await _mobile_fold_finding(*call, finding)
    return None, (flow_plan, route_plan), StepCall(args, kwargs), finding_note


# ``handle_submit_mobile_step``'s text plus its screens.
#
# The ``note`` is saved AFTER the step and only when the step held its run
# (``_MOBILE_STEP_HELD``): a refused or raising call leaves no note.
#
# ``flow`` / ``flow_params`` / ``save_flow`` are popped and resolved into the script BEFORE
# the step, so a refused flow call never reaches the device.
async def handle_submit_mobile_step_content(*args, **kwargs) -> tuple:
    """``handle_submit_mobile_step``'s text plus its screens."""
    early, plans, call, finding_note = await _planned(args, kwargs)
    if early is not None:
        return early
    held, tokens = _arm_step(call.kwargs, plans[1])
    try:
        text, specs = await _run_step(call, plans, held)
        return finding_note + text, specs
    finally:
        _disarm_step(tokens)
