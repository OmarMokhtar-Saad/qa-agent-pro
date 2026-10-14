"""Charter intake packet text and schema pieces (server-owned wording)."""

from __future__ import annotations

_INTAKE_INSTRUCTION = (
    "Put these questions to the TESTER in your own words, in "
    "one message, and ask no others -- the server owns the "
    "question set so that two runs of the same exploration "
    "are comparable. Then call `qa_mobile_test` again with "
    "`charter` set to a JSON object using these exact field "
    "names. Anything the tester does not answer takes the "
    "default shown, and the report NAMES every default that "
    "was used, so leaving a field out is a recorded choice "
    "rather than a silent one."
)

_INTAKE_NEVER_DO = (
    "Never put a password, an OTP or any personal VALUE "
    "in this charter, in any field: the charter is written "
    "into the run's report on disk, and it has no field for "
    "a secret. When the app asks for one during the run, the "
    "packet asks the tester for that field by name and the "
    "value is typed straight to the device -- it never "
    "passes through you, a packet, the report or the run "
    "store."
)


def _scope_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "include": {"type": "array", "items": {"type": "string"}},
            "exclude": {"type": "array", "items": {"type": "string"}},
        },
    }


def _budget_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "steps": {"type": "integer"},
            "minutes": {"type": "integer"},
        },
    }
