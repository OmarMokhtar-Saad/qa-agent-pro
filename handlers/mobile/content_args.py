"""Call shapes and argument readers shared by the mobile content handlers."""

from __future__ import annotations

import inspect
import json
import logging
from typing import NamedTuple

from config.settings_guard import never_raise
from handlers.mobile import run as mobile_run
from handlers.mobile import step as mobile_step
from tools.mobile import held_inputs
from tools.untrusted import single_line

logger = logging.getLogger("tools.mcp_handlers")

_FLOW_WARN = chr(0x26A0) + chr(0xFE0F) + " "


class StepCall(NamedTuple):
    """The ``(args, kwargs)`` of one step or test call."""

    args: tuple
    kwargs: dict


class FlowSpec(NamedTuple):
    """The flow (or route) arguments of one call: replay *name*, or save as *save_name*."""

    name: str
    params: object
    save_name: str


class Draft(NamedTuple):
    """A flow or route call that passed the argument checks."""

    spec: FlowSpec
    pkg: str
    params: object
    script: str


def _step_arg(call: StepCall, name: str) -> str:
    """*name* of a ``handle_submit_mobile_step`` call, as ``_call_arg`` reads it."""
    return mobile_step._call_arg(mobile_step.handle_submit_mobile_step, call, name)


def _test_arg(call: StepCall, name: str) -> str:
    """*name* of a ``handle_mobile_test`` call, as ``_call_arg`` reads it."""
    return mobile_step._call_arg(mobile_run.handle_mobile_test, call, name)


def _mobile_arg(args: tuple, kwargs: dict, name: str):
    """*name*'s RAW value in a ``handle_mobile_test`` call, however it was passed.

    Unlike ``_call_arg`` (which returns ``str``) the type survives, so
    ``apply=False`` stays ``False`` instead of becoming the truthy ``"False"``.
    Found by the handler's own signature, not by position. ``None`` when absent.
    """
    if name in kwargs:
        return kwargs[name]

    names = list(inspect.signature(mobile_run.handle_mobile_test).parameters)
    index = names.index(name)
    return args[index] if index < len(args) else None


def _refusal_plan(why: str) -> dict:
    """The plan of a flow or route call that must not run."""
    return {"refusal": _FLOW_WARN + single_line(why, 400) + " Nothing was touched."}


def _with_script(args: tuple, kwargs: dict, script: str) -> tuple:
    """``(args, kwargs)`` with the script slot (third positional, or the keyword) replaced."""
    if len(args) > 2:
        return args[:2] + (script,) + args[3:], kwargs
    return args, {**kwargs, "script": script}


def _inputs_names(call: StepCall) -> set:
    """The secret field names the tester supplied on this call."""
    names: set = set()
    field = _step_arg(call, "tester_input_field").strip()
    if field:
        names.add(field)
    try:
        loaded = json.loads(_step_arg(call, "tester_inputs") or "{}")
        if isinstance(loaded, dict):
            names.update(str(key) for key in loaded)
    except ValueError:
        logger.debug("tester_inputs arg unreadable", exc_info=True)
    return names


def _held_names(call: StepCall, held_inputs) -> set:
    """The secret field names the run already holds."""
    with never_raise(logger, "held tester inputs unreadable", exc_info=True):
        return {str(key) for key in held_inputs.recall(_step_arg(call, "run_id"))}
    return set()


def _mobile_flow_names(args: tuple, kwargs: dict) -> set:
    """Secret FIELD NAMES the tester supplied on this call or holds for the run. Names only."""

    call = StepCall(args, kwargs)
    return _inputs_names(call) | _held_names(call, held_inputs)


def _mobile_prepare_target(
    call: StepCall, raw_params: object, label: str
) -> tuple[str, object, str]:
    """``(package, params, refusal)`` for a flow or route call; *label* is the noun."""
    pkg, _rid, refusal = mobile_step._mobile_note_target(_step_arg(call, "run_id"), "")
    if refusal:
        return "", {}, label + " not run: " + refusal + "."
    params: object = {}
    if str(raw_params or "").strip():
        try:
            params = json.loads(str(raw_params))
        except ValueError:
            return pkg, {}, "`flow_params` is not valid JSON."
    return pkg, params, ""


def _pop_flow_route_args(kwargs: dict) -> tuple:
    """Pop the flow/route/finding kwargs, in the order the tool reads them."""
    flow_name = str(kwargs.pop("flow", "") or "").strip()
    flow_params = kwargs.pop("flow_params", "")
    save_flow = str(kwargs.pop("save_flow", "") or "").strip()
    route_name = str(kwargs.pop("route", "") or "").strip()
    save_route = str(kwargs.pop("save_route", "") or "").strip()
    finding = str(kwargs.pop("finding", "") or "").strip()
    return flow_name, flow_params, save_flow, route_name, save_route, finding
