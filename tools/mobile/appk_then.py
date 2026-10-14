"""Cleaning the ``then`` half of a knowledge note: waits and precondition steps."""

from __future__ import annotations

from typing import Any

from tools.mobile import actions, appk_consts, knowledge_limits
from tools.mobile.appk_when import _clean


def _until_fields(then: dict) -> tuple:
    clean: dict = {}
    for key in ("until_rid", "until_text", "until_rid_text"):
        if key in then:
            value, problem = _clean(then[key], "then." + key)
            if problem:
                return {}, problem
            if value:
                clean[key] = value
    return clean, ""


def _until_gone(then: dict, clean: dict) -> tuple:
    if "until_gone" not in then:
        return {}, ""
    if not isinstance(then["until_gone"], bool):
        return {}, "then.until_gone must be true or false"
    if not then["until_gone"]:
        return {}, ""
    if "until_rid" not in clean:
        return {}, "until_gone needs until_rid"
    return {"until_gone": True}, ""


def _wait_ms(then: dict) -> tuple:
    if "ms" not in then:
        return {}, ""
    ms = then["ms"]
    if isinstance(ms, bool) or not isinstance(ms, int):
        return {}, "then.ms must be a whole number"
    if not 0 <= ms <= actions.MAX_WAIT_MS:
        return {}, "then.ms must be from 0 to %d" % actions.MAX_WAIT_MS
    return ({"ms": ms} if ms else {}), ""


def _gone_and_ms(then: dict, clean: dict) -> tuple:
    gone, problem = _until_gone(then, clean)
    if problem:
        return {}, problem
    clean.update(gone)
    if "until_rid_text" in clean and "until_rid" not in clean:
        return {}, "until_rid_text needs until_rid"
    wait, problem = _wait_ms(then)
    if problem:
        return {}, problem
    clean.update(wait)
    return clean, ""


def _validate_then(then: dict, has_when_target: bool) -> tuple:
    if not has_when_target:
        return {}, "a wait note needs when.rid or when.activity"
    unknown = sorted(str(key) for key in then if key not in appk_consts._THEN_KEYS)
    if unknown:
        return {}, "unknown then key(s): " + ", ".join(unknown)
    clean, problem = _until_fields(then)
    if problem:
        return {}, problem
    if ("until_rid" in clean) == ("until_text" in clean):
        return {}, "then needs exactly one of until_rid or until_text"
    return _gone_and_ms(then, clean)


def _steps_problem(then: dict) -> list | str:
    extra = sorted(str(key) for key in then if key != "steps")
    if extra:
        return "unknown then key(s): " + ", ".join(extra)
    steps = then.get("steps")
    if not isinstance(steps, list) or not steps:
        return "a precondition needs then.steps"
    if len(steps) > knowledge_limits.PRECONDITION_STEPS:
        return "a precondition has at most %d steps" % (
            knowledge_limits.PRECONDITION_STEPS
        )
    return steps


def _clean_step(step: Any) -> tuple:
    if not isinstance(step, dict) or not appk_consts._OP_RE.match(
        str(step.get("op", ""))
    ):
        return {}, "each precondition step needs an `op` name"
    item: dict = {}
    for key, value in step.items():
        if not isinstance(key, str) or isinstance(value, (dict, list)):
            return {}, "precondition step values must be text, numbers or booleans"
        if isinstance(value, str):
            value, problem = _clean(value, "step." + key)
            if problem:
                return {}, problem
        item[key] = value
    return item, ""


def _validate_steps(then: dict) -> tuple:
    """``(steps, error)`` for a precondition's ``then={"steps": [...]}``."""
    steps = _steps_problem(then)
    if isinstance(steps, str):
        return [], steps
    clean = []
    for step in steps:
        item, problem = _clean_step(step)
        if problem:
            return [], problem
        clean.append(item)
    return clean, ""
