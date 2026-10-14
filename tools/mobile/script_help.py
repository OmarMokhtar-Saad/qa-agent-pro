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

from tools.mobile import action_schema
from tools.mobile.script_help_doc import doc_lines
from tools.mobile.script_help_ex import (  # noqa: F401
    _MODELS,
    MAX_EXAMPLE_CHARS,
    MULTI_ACTION_EXAMPLE,
    OP_EXAMPLES,
    TARGET_KEYS,
    _dumps,
    op_schema,
    signature,
    target_hint,
)
from tools.mobile.script_help_fix import corrected_example, failing_action

#: Cap on the help appended to a refusal; the head keeps action_consts.MAX_ERROR_CHARS.
MAX_HELP_CHARS = 900
#: Cap on the format text embedded in the tool description.
MAX_FORMAT_DOC_CHARS = 2400


def generic_help() -> str:
    """The op format, for a refusal that names no single failing action."""
    text = (
        "Format: "
        + _dumps({"actions": [OP_EXAMPLES["tap"]]})
        + ". Each action is an object whose op is one of: "
        + ", ".join(action_schema.OPS)
        + ". Selectors go inside target, never beside op. "
        + target_hint()
    )
    return text[:MAX_HELP_CHARS]


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
    return "\n".join(doc_lines())[:MAX_FORMAT_DOC_CHARS]


SCRIPT_FORMAT_DOC = format_doc()
