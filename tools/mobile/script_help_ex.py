"""Examples and field shapes behind the script format help (leaf of script_help)."""

from __future__ import annotations

import json
from typing import get_args

from tools.mobile import action_schema
from tools.mobile.action_models_basic import Target

#: Longest corrected example built from the model's own values; past it the stock
#: example is shown instead.
MAX_EXAMPLE_CHARS = 240

TARGET_KEYS: tuple = tuple(Target.model_fields)

_MODELS: dict = {
    op: model
    for model in get_args(get_args(action_schema.Action)[0])
    for op in get_args(model.model_fields["op"].annotation)
}

#: One valid action per op. A test pins that every entry validates and that the
#: keys equal action_schema.OPS, so a new op cannot ship without its example.
OP_EXAMPLES: dict = {
    "tap": {"op": "tap", "target": {"label": "Sign in"}},
    "tap_text": {"op": "tap_text", "text": "Sign in"},
    "tap_text_if_present": {
        "op": "tap_text_if_present",
        "text": "Not now",
        "max_s": 2,
    },
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
