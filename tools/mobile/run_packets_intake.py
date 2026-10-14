"""The charter intake packet: the first packet of an explore run."""

from __future__ import annotations

from types import ModuleType
from typing import Any

from tools.mobile import charter as charter_mod
from tools.mobile.run_packets_base import _packet_base
from tools.mobile.run_packets_intake_text import (
    _INTAKE_INSTRUCTION,
    _INTAKE_NEVER_DO,
    _budget_schema,
    _scope_schema,
)

#: The FIRST packet of an explore run. A packet KIND on the existing return
#: path, deliberately NOT a tool: the test for whether something earns a tool
#: is whether the model could call it out of order and still be right, and
#: intake is only ever first.
CHARTER_INTAKE_KIND = "charter_intake"


def _intake_questions(charter_mod: ModuleType, asks: Any) -> list[dict]:
    return [
        {
            "field": str(question["field"]),
            "ask": str(question["ask"]),
            "options": list(question["options"]),
            "default": charter_mod.defaults()[question["field"]],
        }
        for question in asks
    ]


def _unasked_fields(charter_mod: ModuleType) -> list:
    return [
        field for field in charter_mod.defaults() if field not in charter_mod.ASKABLE
    ]


def _intake_texts() -> dict:
    return {"instruction": _INTAKE_INSTRUCTION, "never_do": _INTAKE_NEVER_DO}


def _intake_response_schema(charter_mod: ModuleType) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["goal"],
        "properties": {
            "goal": {"type": "string"},
            "depth": {"enum": list(charter_mod.DEPTHS)},
            "scope": _scope_schema(),
            "destructive": {"enum": list(charter_mod.DESTRUCTIVE)},
            "budget": _budget_schema(),
            "stop_on": {"enum": list(charter_mod.STOP_ON)},
        },
    }


# THE RAW CHARTER, NOT A NORMALISED ONE. ``normalize`` fills every
# field, so ``questions_for(normalize({}))`` returns ONE question
# (``goal``) where ``questions_for({})`` returns FIVE -- measured. This
# line held the normalised form, so a tester with no charter was asked
# ONE question and depth, scope, destructive and budget were silently
# defaulted. Same defect as judging the normalised charter in
# ``defaults_used`` (M2d), at a SECOND CONSUMER: the function was
# graded, the WIRING was not. Graded at this seam by M1c /
# ``test_an_empty_intake_asks_all_five_questions_through_the_builder``.
def _charter_intake_packet(run_id: str, package: str, charter: object) -> dict:
    """The intake packet itself; ``build_charter_intake`` guards it."""
    asks = charter_mod.questions_for(charter)
    packet = _packet_base(run_id)
    packet.update(
        {
            "kind": CHARTER_INTAKE_KIND,
            "package": str(package or "")[:200],
            "questions": _intake_questions(charter_mod, asks),
            "defaults": charter_mod.defaults(),
            "unasked_fields_take_their_default": _unasked_fields(charter_mod),
            **_intake_texts(),
            "response_schema": _intake_response_schema(charter_mod),
        }
    )
    return packet
