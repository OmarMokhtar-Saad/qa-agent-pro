"""Checks that turn a script into a saved-flow template (see ``app_flows``)."""

from __future__ import annotations

import copy
import json
import logging
from typing import Any

from tools.mobile import actions, app_knowledge
from tools.mobile.app_flows_consts import (
    ALLOWED_OPS,
    CREDENTIAL_WORDS,
    MAX_FIELD_CHARS,
    MAX_PARAMS_PER_FLOW,
    PLACEHOLDER_RE,
    REFUSED_WHY,
    err,
)
from tools.mobile.flow_roles import role_problem

logger = logging.getLogger("tools.mobile.app_flows")


def _check_secret(index: int, step: dict) -> str:
    text = str(step.get("text") or "")
    field = str(step.get("field") or "")
    if not field.strip() or len(field) > MAX_FIELD_CHARS:
        return (
            "step %d: a secret step needs a `field` name of at most %d characters"
            % (
                index,
                MAX_FIELD_CHARS,
            )
        )
    why = app_knowledge.secret_reason(field)
    if why:
        return "step %d is not saved: its `field` name is refused: %s" % (index, why)
    if text:
        return (
            "step %d: a secret step carries no `text`; its value comes from the tester at replay"
            % index
        )
    return ""


def _credential_problem(index: int, name: str) -> str:
    if any(word in CREDENTIAL_WORDS for word in name.split("_")):
        return (
            "step %d: parameter `%s` is named like a credential; use a secret step with a `field`"
            % (index, name)
        )
    return ""


def _check_placeholder(index: int, step: dict, params: list) -> str:
    found = PLACEHOLDER_RE.fullmatch(str(step.get("text") or ""))
    if not found:
        return (
            "step %d: typed text must be one {placeholder}, or a secret step with a `field`; "
            "a flow never stores a typed value" % index
        )
    name = found.group(1)
    problem = _credential_problem(index, name) or role_problem(index, step, name)
    if not problem and name not in params:
        params.append(name)
    return problem


def _check_typed(index: int, step: dict, params: list) -> str:
    if step.get("secret"):
        return _check_secret(index, step)
    return _check_placeholder(index, step, params)


def _screen_strings(step: dict) -> list:
    out: list = []
    target = step.get("target")
    if isinstance(target, dict):
        out += [target.get(key) for key in ("text", "label", "rid")]
    if step.get("op") == "fill":
        out.append(step.get("label"))
    elif step.get("op") != "type":
        out.append(step.get("text"))
    return [str(value) for value in out if value]


def _op_problem(index: int, op: str) -> str:
    if op in ALLOWED_OPS:
        return ""
    why = REFUSED_WHY.get(op, "it is not a replayable UI action")
    return "step %d: `%s` cannot be saved in a flow (%s)" % (index, op[:30], why)


def _target_problem(index: int, clean: dict) -> str:
    target = clean.get("target")
    if isinstance(target, dict):
        target.pop("id", None)
        if not any(
            str(target.get(key) or "").strip()
            for key in ("text", "rid", "role", "label")
        ):
            return (
                "step %d: its target is a positional id only, which does not survive a replay"
                % index
            )
    return ""


def _typed_problem(index: int, clean: dict, params: list) -> str:
    if str(clean.get("op") or "") in ("type", "fill"):
        return _check_typed(index, clean, params)
    return ""


def _screen_problem(index: int, clean: dict) -> str:
    for value in _screen_strings(clean):
        why = app_knowledge.secret_reason(value)
        if why:
            return "step %d is not saved: %s" % (index, why)
    return ""


def _check_step(index: int, step: Any, params: list) -> tuple[dict, str]:
    """``(clean_step, "")`` for a replayable step, else ``({}, problem)``."""
    if not isinstance(step, dict):
        return {}, "step %d is not an object" % index
    problem = _op_problem(index, str(step.get("op") or ""))
    if problem:
        return {}, problem
    clean = copy.deepcopy(step)
    problem = (
        _target_problem(index, clean)
        or _typed_problem(index, clean, params)
        or _screen_problem(index, clean)
    )
    return ({}, problem) if problem else (clean, "")


def _template_payload(raw: Any) -> tuple:
    """The script's action list and no problem, or None and the refusal text."""
    payload = raw
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return None, "the script is not valid JSON"
    if isinstance(payload, dict):
        payload = payload.get("actions")
    if not isinstance(payload, list) or not payload:
        return None, "a flow needs a non-empty `actions` list"
    if len(payload) > actions.MAX_MODEL_ACTIONS:
        return None, "a flow holds at most %d steps" % actions.MAX_MODEL_ACTIONS
    return payload, None


def _collect_steps(payload: list) -> tuple[list, list, str]:
    steps: list = []
    params: list = []
    for index, step in enumerate(payload, 1):
        clean, problem = _check_step(index, step, params)
        if problem:
            return steps, params, problem
        steps.append(clean)
    if len(params) > MAX_PARAMS_PER_FLOW:
        return steps, params, "a flow takes at most %d parameters" % MAX_PARAMS_PER_FLOW
    return steps, params, ""


def _parsed(steps: list, params: list) -> dict:
    parsed = actions.parse_script({"actions": steps})
    if parsed.get("error"):
        return err(str(parsed["error"])[:300])
    return {"error": None, "content": {"params": params, "steps": steps}}


def template_from_script(raw: Any) -> dict:
    """A validated template ``{'params', 'steps'}`` from a script (JSON text, list or dict).

    Refuses an op that cannot be replayed blind, a literal typed value, a target that is only
    a positional id, a credential-looking parameter name, and any screen string the note
    scrub would refuse. The caller's data is never mutated.
    """
    try:
        payload, problem = _template_payload(raw)
        if problem:
            return err(problem)
        steps, params, problem = _collect_steps(payload)
        if problem:
            return err(problem)
        return _parsed(steps, params)
    except Exception:
        logger.exception("mobile.app_flows.template_from_script failed")
        return err("the flow could not be checked")


def secret_fields(template: dict) -> list:
    return sorted(
        {str(s.get("field")) for s in template.get("steps") or [] if s.get("secret")}
    )
