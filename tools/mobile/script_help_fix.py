"""Corrected-example builder behind script_help_fix (leaf of script_help)."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from tools.mobile import action_schema
from tools.mobile.script_help_ex import (
    _MODELS,
    MAX_EXAMPLE_CHARS,
    OP_EXAMPLES,
    TARGET_KEYS,
    _dumps,
)


def _raw_action(payload: object, index: int) -> object:
    listed = payload.get("actions") if isinstance(payload, dict) else None
    if isinstance(listed, list) and 0 <= index < len(listed):
        return listed[index]
    return None


def failing_action(errors: Any, payload: object) -> tuple:
    """``(op, raw_action)`` of the first problem that sits inside an action.

    The op comes from the submitted action when it is text, else from the error
    location. Either may be None; nothing here raises on a hostile payload.
    """
    for problem in errors or ():
        loc = tuple(problem.get("loc") or ()) if isinstance(problem, dict) else ()
        if len(loc) < 2 or loc[0] != "actions" or not isinstance(loc[1], int):
            continue
        raw = _raw_action(payload, loc[1])
        op = raw.get("op") if isinstance(raw, dict) else None
        if not isinstance(op, str) and len(loc) > 2 and isinstance(loc[2], str):
            op = loc[2]
        return (op if isinstance(op, str) else None), raw
    return None, None


def _stray_selectors(raw: dict, own: set) -> dict:
    return {
        k: raw[k]
        for k in TARGET_KEYS
        if k in raw and k not in own and isinstance(raw[k], str)
    }


def _validated(model: Any, fixed: dict) -> str:
    try:
        model.model_validate(fixed)
    except ValidationError:
        return ""
    text = _dumps(fixed)
    return text if len(text) <= MAX_EXAMPLE_CHARS else ""


def _hoist_selector(op: str, raw: dict) -> str:
    """*raw* with stray top-level selectors moved into target, as compact JSON.

    A selector that is one of the model's OWN fields stays (``assert`` has a
    ``text``). Empty when the result is not a valid action or is too long.
    """
    model = _MODELS[op]
    own = set(model.model_fields)
    if "target" not in own:
        return ""
    stray = _stray_selectors(raw, own)
    target = raw.get("target")
    fixed = {k: v for k, v in raw.items() if k not in stray}
    fixed["target"] = {**(target if isinstance(target, dict) else {}), **stray}
    return _validated(model, fixed)


def corrected_example(op: str, raw: object = None) -> str:
    """A valid action of *op* as compact JSON: the model's own, rearranged, when
    that works and the op carries no typed value; the stock example otherwise."""
    if isinstance(raw, dict) and op not in action_schema.VALUE_BEARING_OPS:
        rearranged = _hoist_selector(op, raw)
        if rearranged:
            return rearranged
    return _dumps(OP_EXAMPLES[op])
