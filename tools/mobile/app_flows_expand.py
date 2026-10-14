"""Turns a saved-flow template back into a replayable script (see ``app_flows``)."""

from __future__ import annotations

import copy
import json
import logging
from typing import Any

from tools.mobile import actions
from tools.mobile.app_flows_check import secret_fields
from tools.mobile.app_flows_consts import PLACEHOLDER_RE, err

logger = logging.getLogger("tools.mobile.app_flows")


def _params_problem(template: dict, params: dict) -> str:
    """Why *params* do not fit the template's declared parameters, or ``''``."""
    declared = list(template.get("params") or [])
    missing = [p for p in declared if p not in params]
    extra = sorted(str(k)[:40] for k in params if k not in declared)
    if missing or extra:
        return "flow parameters do not match: missing %s, unknown %s" % (
            missing or "none",
            extra or "none",
        )
    for key, value in params.items():
        if not isinstance(value, str) or len(value) > actions.MAX_TEXT_CHARS:
            return "parameter `%s` must be text of at most %d characters" % (
                str(key)[:40],
                actions.MAX_TEXT_CHARS,
            )
    return ""


def _expand_problem(template: dict, params: Any, supplied: Any) -> str:
    if not isinstance(params, dict):
        return "`flow_params` must be a JSON object"
    problem = _params_problem(template, params)
    if problem:
        return problem
    absent = [f for f in secret_fields(template) if f not in set(supplied or ())]
    if absent:
        return "the tester has not supplied a value for secret field(s) %s" % absent
    return ""


def _filled_steps(template: dict, params: dict) -> list:
    steps = copy.deepcopy(template.get("steps") or [])
    for step in steps:
        if step.get("op") in ("type", "fill") and not step.get("secret"):
            found = PLACEHOLDER_RE.fullmatch(str(step.get("text") or ""))
            step["text"] = params[found.group(1)] if found else ""
    return steps


def expand(template: dict, params: Any, supplied: Any) -> dict:
    """The script JSON to replay: placeholders filled, then parsed again as the final gate.

    Refuses BEFORE any device work when a parameter is missing or unknown, or when a secret
    step's field has no value supplied. *supplied* is a collection of field NAMES only.
    """
    try:
        if params is None or params == "":
            params = {}
        problem = _expand_problem(template, params, supplied)
        if problem:
            return err(problem)
        steps = _filled_steps(template, params)
        parsed = actions.parse_script({"actions": steps})
        if parsed.get("error"):
            return err(str(parsed["error"])[:300])
        return {"error": None, "content": json.dumps({"actions": steps})}
    except Exception:
        logger.exception("mobile.app_flows.expand failed")
        return err("the flow could not be expanded")
