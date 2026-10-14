"""Route calls: turn a ``route`` or ``save_route`` argument into a script, then the save line."""

from __future__ import annotations

import logging

from handlers.mobile.content_args import (
    Draft,
    FlowSpec,
    StepCall,
    _mobile_flow_names,
    _mobile_prepare_target,
    _refusal_plan,
    _step_arg,
)
from tools.mobile import app_flows, app_routes
from tools.untrusted import single_line

logger = logging.getLogger("tools.mcp_handlers")


def _route_prepare_checks(name: str, save_name: str, script: str) -> str:
    """The refusal text for a route call whose arguments contradict, or ``""``."""

    if name and save_name:
        return "Pass `route` (replay a saved route) OR `save_route` (save this script as one), not both."
    if name and script.strip():
        return "`route` replays a saved route; leave `script` empty."
    if save_name and not script.strip():
        return "`save_route` saves this call's `script`; pass one."
    if save_name and not app_flows.valid_name(save_name):
        return "`save_route` is not a valid route name."
    return ""


def _route_built(call: StepCall, draft: Draft, template: dict) -> dict:
    """The plan of a checked route: its script expanded, or a refusal."""

    built = app_flows.expand(template, draft.params, _mobile_flow_names(*call))
    if built.get("error"):
        return _refusal_plan("Route not run: " + str(built["error"]) + ".")
    save_name = draft.spec.save_name
    return {
        "script": built["content"],
        "name": draft.spec.name or save_name,
        "package": draft.pkg,
        "save": bool(save_name),
        "steps": len(template["steps"]),
        "template": template,
        "expect": [] if save_name else list(template["screens"]),
        "end": "" if save_name else str(template["end"]),
    }


def _route_resolve(call: StepCall, draft: Draft, routes) -> dict:
    """Load or read the route template, check a save, then expand it."""

    name, save_name = draft.spec.name, draft.spec.save_name
    got = (
        routes.get_route(draft.pkg, name)
        if name
        else app_flows.template_from_script(draft.script)
    )
    if got.get("error"):
        return _refusal_plan("Route not run: " + str(got["error"]) + ".")
    template = got["content"]
    problem = routes.check_save(draft.pkg, save_name, template) if save_name else ""
    if problem:
        return _refusal_plan("Route not run: " + problem + ".")
    return _route_built(call, draft, template)


def _route_plan(call: StepCall, spec: FlowSpec, routes) -> dict:
    """The plan of a route call; every refusal comes before anything is resolved."""
    script = _step_arg(call, "script")
    why = _route_prepare_checks(spec.name, spec.save_name, script)
    if why:
        return _refusal_plan(why)
    pkg, params, why = _mobile_prepare_target(call, spec.params, "Route")
    if why:
        return _refusal_plan(why)
    return _route_resolve(call, Draft(spec, pkg, params, script), routes)


# Turn a route call into the script to replay, or ``{'refusal': text}`` when nothing may
# run. Every refusal comes BEFORE the device is touched. Never raises. A replay carries the
# saved screens as ``expect``; a save carries none (it records them from the trace).
def _mobile_route_prepare(call: StepCall, spec: FlowSpec) -> dict:
    """Turn a route call into the script to replay, or ``{'refusal': text}``."""

    try:
        return _route_plan(call, spec, app_routes)
    except Exception:
        logger.exception("mobile route: prepare failed")
        return _refusal_plan("Route not run: the route store is unavailable.")


def _route_save_line(plan: dict, held: dict, case: dict) -> str:
    """The line of a route call that asked to save: saved, or why not."""

    count = int(plan.get("steps") or 0)
    name = plan["name"]
    if not held.get("held"):
        return "Route not saved: this call did not run a step.\n\n"
    recorded = app_routes.record_from_case(case, count)
    if recorded.get("error"):
        return "Route `%s` not saved: %s.\n\n" % (
            name,
            single_line(recorded["error"], 300),
        )
    seen = recorded["content"]
    spec = app_routes.RouteSpec(seen["screens"], seen["end"], plan["template"])
    saved = app_routes.save_route(plan["package"], name, spec)
    if saved.get("error"):
        return "Route not saved: " + single_line(saved["error"], 300) + "\n\n"
    return "Saved route `%s` for `%s` (%d steps).\n\n" % (name, plan["package"], count)


def _route_replay_line(plan: dict, held: dict, case: dict) -> str:
    """The line of a route replay: empty without a step, else matched or the stop line."""

    count = int(plan.get("steps") or 0)
    name = plan["name"]
    if not held.get("held"):
        return ""
    spec = app_routes.RouteSpec(plan.get("expect"), plan.get("end"))
    if app_routes.replay_matched(case, count, spec):
        return (
            "Route `%s` replayed: every screen matched the saved route (%d steps).\n\n"
            % (name, count)
        )
    return app_routes.stop_line(name, case) + "\n\n"


# The line a route call prepends to the step reply. Saves a new route ONLY after a clean
# replay whose every step recorded the screens it started and ended on. Never raises.
def _mobile_route_finish(plan: dict, held: dict) -> str:
    """The line a route call prepends to the step reply. Never raises."""
    try:
        case: dict = held["case"] if isinstance(held.get("case"), dict) else {}
        if plan.get("save"):
            return _route_save_line(plan, held, case)
        return _route_replay_line(plan, held, case)
    except Exception:
        logger.exception("mobile route: finish failed")
        return ""
