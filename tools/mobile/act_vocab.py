"""The vocabulary handed to the model: its machine-readable spec and its JSON schema."""

from __future__ import annotations

import logging

from tools.mobile.act_notes import _action_notes, _budget_notes, _wait_notes
from tools.mobile.act_schema_trim import trim_keeping
from tools.mobile.action_consts import MAX_MODEL_ACTIONS
from tools.mobile.action_schema import OPS, Script

logger = logging.getLogger("tools.mobile.actions")

#: ``$defs`` whose docstring is the model's only statement of what the validator
#: accepts (``test_the_response_schema_documents_both_operand_shapes`` pins it),
#: or of what a field means (ScrollAction: "down" reveals what is below).
KEEP_DOCS = ("AssertAction", "ScrollAction")


def describe_vocabulary() -> dict:
    """The machine-readable spec handed to the model. Data only, never raises."""
    return {
        "max_actions": MAX_MODEL_ACTIONS,
        "ops": list(OPS),
        "target": (
            "one of {id, rid, role, label, text}, and they are CROSS-CHECKED: "
            "send two that name different elements, or one that matches "
            "nothing beside one that matches, and the action is handed back "
            "rather than guessed. Prefer `role` or `label` for any action that "
            "follows another -- the server computes them from the element's "
            "own content and they survive a re-layout. `rid` is next. The "
            "short id is THIS SCREEN ONLY: it describes the element as it was "
            "on the screen you were given, bounds included, so it stops "
            "matching when that element moves. It can never match a DIFFERENT "
            "element, but being handed back still costs you a turn."
        ),
        "notes": _action_notes() + _wait_notes() + _budget_notes(),
    }


def response_schema() -> dict:
    """JSON schema for a script, for the packet's ``response_schema`` field.

    ``Script`` keeps the saved cap (:data:`MAX_ACTIONS`) because a folded queue
    may reach it, but the model is held to :data:`MAX_MODEL_ACTIONS`, so the
    schema it is shown says that one -- one cap, not two.
    """
    try:
        schema = Script.model_json_schema()
        schema["properties"]["actions"]["maxItems"] = MAX_MODEL_ACTIONS
        return trim_keeping(schema, KEEP_DOCS)
    except Exception:  # pragma: no cover - defensive
        logger.exception("mobile.actions.response_schema failed")
        return {"type": "object"}
