"""Teach the model the step format: in every refusal, and in the tool text.

One source for both surfaces. The ops and the caps come from ``actions`` itself,
so a new op or a changed cap shows up here without a second edit. A refusal used
to say only which field failed, and a model that had guessed a flat shape
guessed it again. Now it also carries a corrected example of the op that failed:
built from the model's own values when they can be rearranged into a valid
action, a stock example otherwise.

A value-bearing op (type, fill, ask_tester) is never echoed back from the
model's own values, so a typed credential cannot ride in a refusal.
"""

from __future__ import annotations

import json
from typing import get_args

from pydantic import ValidationError

from tools.mobile import actions, launch_guard

#: Cap on the help appended to a refusal; the head keeps actions.MAX_ERROR_CHARS.
MAX_HELP_CHARS = 900
#: Cap on the format text embedded in the tool description.
MAX_FORMAT_DOC_CHARS = 2400
#: Longest corrected example built from the model's own values; past it the stock
#: example is shown instead.
MAX_EXAMPLE_CHARS = 240

TARGET_KEYS: tuple = tuple(actions.Target.model_fields)

_MODELS: dict = {
    op: model
    for model in get_args(get_args(actions.Action)[0])
    for op in get_args(model.model_fields["op"].annotation)
}

#: One valid action per op. A test pins that every entry validates and that the
#: keys equal actions.OPS, so a new op cannot ship without its example.
OP_EXAMPLES: dict = {
    "tap": {"op": "tap", "target": {"label": "Sign in"}},
    "tap_text": {"op": "tap_text", "text": "Sign in"},
    "type": {"op": "type", "target": {"label": "Email"}, "text": "hello"},
    "fill": {"op": "fill", "label": "Email", "text": "hello"},
    "clear_app_data": {"op": "clear_app_data"},
    "clear": {"op": "clear", "target": {"label": "Email"}},
    "back": {"op": "back"},
    "home": {"op": "home"},
    "press": {"op": "press", "key": "enter", "target": {"label": "Search"}},
    "scroll": {"op": "scroll", "dir": "down"},
    "wait": {"op": "wait", "ms": 1000},
    "wait_until_changed": {"op": "wait_until_changed", "max_s": 10},
    "wait_until_text": {"op": "wait_until_text", "text": "Welcome", "max_s": 10},
    "wait_until_gone": {"op": "wait_until_gone", "text": "Loading", "max_s": 10},
    "wait_until_idle": {"op": "wait_until_idle", "max_s": 10},
    "launch": {"op": "launch"},
    "open_url": {"op": "open_url", "url": "https://example.com"},
    "assert": {"op": "assert", "kind": "text_present", "text": "Welcome"},
    "ask_tester": {"op": "ask_tester", "prompt": "Enter the password", "field": "pw"},
    "done": {"op": "done", "verdict": "pass", "reason": "The flow completed"},
}

MULTI_ACTION_EXAMPLE: dict = {
    "actions": [
        {"op": "tap", "target": {"label": "Sign in"}},
        {"op": "wait_until_changed", "max_s": 10},
        {"op": "assert", "kind": "new_text"},
    ]
}


def _dumps(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def op_schema(op: str) -> tuple:
    """The (required, optional) field names of *op*, ``op`` itself left out."""
    model = _MODELS.get(op)
    fields = {} if model is None else model.model_fields
    required = [n for n, f in fields.items() if n != "op" and f.is_required()]
    optional = [n for n, f in fields.items() if n != "op" and not f.is_required()]
    return required, optional


def signature(op: str) -> str:
    required, optional = op_schema(op)
    return (
        "`"
        + op
        + "` takes required: "
        + (", ".join(required) or "nothing")
        + "; optional: "
        + (", ".join(optional) or "nothing")
        + "."
    )


def target_hint() -> str:
    return (
        "A target is an object holding one or more of: " + ", ".join(TARGET_KEYS) + "."
    )


def generic_help() -> str:
    """The op format, for a refusal that names no single failing action."""
    text = (
        "Format: "
        + _dumps({"actions": [OP_EXAMPLES["tap"]]})
        + ". Each action is an object whose op is one of: "
        + ", ".join(actions.OPS)
        + ". Selectors go inside target, never beside op. "
        + target_hint()
    )
    return text[:MAX_HELP_CHARS]


def _raw_action(payload: object, index: int) -> object:
    listed = payload.get("actions") if isinstance(payload, dict) else None
    if isinstance(listed, list) and 0 <= index < len(listed):
        return listed[index]
    return None


def failing_action(errors: object, payload: object) -> tuple:
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


def _hoist_selector(op: str, raw: dict) -> str:
    """*raw* with stray top-level selectors moved into target, as compact JSON.

    A selector that is one of the model's OWN fields stays (``assert`` has a
    ``text``). Empty when the result is not a valid action or is too long.
    """
    model = _MODELS[op]
    own = set(model.model_fields)
    if "target" not in own:
        return ""
    stray = {
        k: raw[k]
        for k in TARGET_KEYS
        if k in raw and k not in own and isinstance(raw[k], str)
    }
    target = raw.get("target")
    fixed = {k: v for k, v in raw.items() if k not in stray}
    fixed["target"] = {**(target if isinstance(target, dict) else {}), **stray}
    try:
        model.model_validate(fixed)
    except ValidationError:
        return ""
    text = _dumps(fixed)
    return text if len(text) <= MAX_EXAMPLE_CHARS else ""


def corrected_example(op: str, raw: object = None) -> str:
    """A valid action of *op* as compact JSON: the model's own, rearranged, when
    that works and the op carries no typed value; the stock example otherwise."""
    if isinstance(raw, dict) and op not in actions.VALUE_BEARING_OPS:
        rearranged = _hoist_selector(op, raw)
        if rearranged:
            return rearranged
    return _dumps(OP_EXAMPLES[op])


def refusal_help(errors: object, payload: object) -> str:
    """What a schema refusal appends: a corrected example on its own first line,
    the failing op's fields, and the target shape; cut at MAX_HELP_CHARS."""
    op, raw = failing_action(errors, payload)
    if op not in _MODELS:
        return generic_help()
    lines = ["Corrected example: " + corrected_example(op, raw), signature(op)]
    if "target" in _MODELS[op].model_fields:
        lines.append(target_hint())
    return "\n".join(lines)[:MAX_HELP_CHARS]


def format_doc() -> str:
    """The step format for the tool description, read live from the constants."""
    finding_cap = actions.TURN_FIELD_SCHEMA["finding"]["maxLength"]
    lines = [
        'Script: {"actions":[...]}, at most '
        + str(actions.MAX_MODEL_ACTIONS)
        + " actions per step.",
        "Ops: " + ", ".join(actions.OPS) + ".",
        "Selectors (" + ", ".join(TARGET_KEYS) + ") go inside a target object, never "
        "beside op. fill takes a label instead of a target.",
        "wait ms <= "
        + str(actions.MAX_WAIT_MS)
        + "; "
        + ", ".join(actions.WAIT_UNTIL_OPS)
        + " take max_s <= "
        + str(actions.WAIT_UNTIL_MAX_S)
        + " (default "
        + str(actions.WAIT_UNTIL_DEFAULT_S)
        + "). One step waits at most "
        + str(actions.MAX_TOTAL_WAIT_MS)
        + " ms in total.",
        "scroll dir: "
        + "|".join(actions.SCROLL_DIRECTIONS)
        + ". assert kind: "
        + "|".join(actions.ASSERT_KINDS)
        + " (visual must be last). done verdict: "
        + "|".join(actions.VERDICTS)
        + ". open_url schemes: "
        + ", ".join(actions.URL_SCHEMES)
        + ".",
        'launch reopens the app under test; {"op":"launch","package":"<id>"} '
        "opens ONE other app: only the App Tester ("
        + launch_guard.APP_TESTER_PACKAGE
        + ") or a package id listed in this "
        "reply expect_apps (at most " + str(actions.MAX_EXPECT_APPS) + " ids).",
        "Reply fields beside actions: "
        + ", ".join(actions.TURN_FIELDS)
        + ". finding <= "
        + str(finding_cap)
        + " chars; typed text <= "
        + str(actions.MAX_TEXT_CHARS)
        + " chars.",
        "Example: " + _dumps(MULTI_ACTION_EXAMPLE),
    ]
    return "\n".join(lines)[:MAX_FORMAT_DOC_CHARS]


SCRIPT_FORMAT_DOC = format_doc()
