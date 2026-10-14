"""Flow calls: turn a ``flow`` or ``save_flow`` argument into a script, then the save line."""

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
from tools.mobile import app_flows
from tools.untrusted import single_line

logger = logging.getLogger("tools.mcp_handlers")


def _flow_prepare_checks(name: str, save_name: str, script: str) -> str:
    """The refusal text for a flow call whose arguments contradict, or ``""``."""
    if name and save_name:
        return "Pass `flow` (replay a saved flow) OR `save_flow` (save this script as one), not both."
    if name and script.strip():
        return "`flow` replays a saved flow; leave `script` empty."
    if save_name and not script.strip():
        return "`save_flow` saves this call's `script`; pass one."
    return ""


def _flow_built(draft: Draft, template: dict, built: dict) -> dict:
    """The plan of a checked flow: its script expanded, or a refusal."""
    if built.get("error"):
        return _refusal_plan("Flow not run: " + str(built["error"]) + ".")
    return {
        "script": built["content"],
        "name": draft.spec.name or draft.spec.save_name,
        "package": draft.pkg,
        "save": bool(draft.spec.save_name),
        "steps": len(template["steps"]),
        "template": template,
    }


def _flow_resolve(call: StepCall, draft: Draft, flows) -> dict:
    """Load or read the flow template, check a save, then expand it."""
    name, save_name = draft.spec.name, draft.spec.save_name
    got = (
        flows.get_flow(draft.pkg, name)
        if name
        else flows.template_from_script(draft.script)
    )
    if not name and not flows.valid_name(save_name):
        return _refusal_plan("`save_flow` is not a valid flow name.")
    if got.get("error"):
        return _refusal_plan("Flow not run: " + str(got["error"]) + ".")
    template = got["content"]
    problem = flows.check_save(draft.pkg, save_name, template) if save_name else ""
    if problem:
        return _refusal_plan("Flow not run: " + problem + ".")
    built = flows.expand(template, draft.params, _mobile_flow_names(*call))
    return _flow_built(draft, template, built)


def _flow_plan(call: StepCall, spec: FlowSpec, flows) -> dict:
    """The plan of a flow call; every refusal comes before anything is resolved."""
    script = _step_arg(call, "script")
    why = _flow_prepare_checks(spec.name, spec.save_name, script)
    if why:
        return _refusal_plan(why)
    pkg, params, why = _mobile_prepare_target(call, spec.params, "Flow")
    if why:
        return _refusal_plan(why)
    return _flow_resolve(call, Draft(spec, pkg, params, script), flows)


# Turn a flow call into the script to replay, or ``{'refusal': text}`` when nothing may
# run. Every refusal comes BEFORE the device is touched. Never raises.
def _mobile_flow_prepare(call: StepCall, spec: FlowSpec) -> dict:
    """Turn a flow call into the script to replay, or ``{'refusal': text}``."""

    try:
        return _flow_plan(call, spec, app_flows)
    except Exception:
        logger.exception("mobile flow: prepare failed")
        return _refusal_plan("Flow not run: the flow store is unavailable.")


def _flow_save_line(plan: dict, held: dict, clean: bool) -> str:
    """The line of a flow call that asked to save: saved, or why not."""

    if not held.get("held"):
        return "Flow not saved: this call did not run a step.\n\n"
    if not clean:
        return (
            "Flow `%s` not saved: the replay did not finish cleanly.\n\n" % plan["name"]
        )
    saved = app_flows.save_flow(plan["package"], plan["name"], plan["template"])
    if saved.get("error"):
        return "Flow not saved: " + single_line(saved["error"], 300) + "\n\n"
    return "Saved flow `%s` for `%s` (%d steps).\n\n" % (
        plan["name"],
        plan["package"],
        int(plan.get("steps") or 0),
    )


# The line a flow call prepends to the step reply. Saves a new flow ONLY after a clean
# replay. Never raises.
def _mobile_flow_finish(plan: dict, held: dict) -> str:
    """The line a flow call prepends to the step reply. Never raises."""

    try:
        count = int(plan.get("steps") or 0)
        case = held.get("case") if isinstance(held.get("case"), dict) else {}
        clean = bool(held.get("held")) and app_flows.clean_replay(case, count)
        if plan.get("save"):
            return _flow_save_line(plan, held, clean)
        if not held.get("held") or clean:
            return ""
        return app_flows.stop_line(plan["name"], case, count) + "\n\n"
    except Exception:
        logger.exception("mobile flow: finish failed")
        return ""
