"""Host mode: the ambiguity job spec and its attachment to a prepare payload (split from host_jobs.py)."""

from __future__ import annotations

import logging

from agents.host_jobs_core import _AMBIGUITY_JOB_INSTRUCTIONS, _COLLISION_CLAUSE
from config.settings_guard import never_raise
from tools.id_collisions import find_identifier_collisions

logger = logging.getLogger("agents.host_mode")


def _ambiguity_response_schema() -> dict:
    """A fresh ``response_schema`` (a new nested dict per call, never shared)."""
    return {
        "type": "object",
        "properties": {
            "severity": {
                "type": "string",
                "enum": ["none", "low", "medium", "high"],
            },
            "issues": {"type": "array", "items": {"type": "string"}},
            "questions": {"type": "array", "items": {"type": "string"}},
            "testable_surface": {
                "type": "string",
                "enum": ["ui", "api", "backend", "docs", "none", "unclear"],
            },
        },
        "required": ["severity", "issues", "questions", "testable_surface"],
    }


def _ambiguity_job_spec() -> dict:
    """A fresh ``ambiguity_job`` payload entry (task, instructions, schema)."""
    return {
        "task": "classify_requirements_before_generating",
        "instructions": (
            "Classify the ticket in user_context BEFORE generating cases. "
            "Return JSON: severity, issues, questions (<=3), testable_surface. "
            "If severity is high (or no-UI with no URL), stop and ask the user; "
            "otherwise continue to generate."
        ),
        "response_schema": _ambiguity_response_schema(),
    }


# DETECT AND REPORT ONLY. This does not resolve the collision, does not
# block generation, and does not touch `host_ambiguity_severity` -- the
# host self-reports that, and the server does not classify. It puts the
# finding in the host's hand BEFORE it generates, which is the one thing
# that was missing on TICKET-5646: the gate rated a self-contradicting
# spec `low` and the generator then split 22 cases against 8 on it.
# The key is OMITTED when there is nothing to say, so a clean ticket's
# payload is key-identical to today's.
def _stamp_ambiguity_job(out: dict) -> None:
    """Add ambiguity_job and the step-0 instructions onto ``out``."""
    out["ambiguity_job"] = _ambiguity_job_spec()
    collisions = find_identifier_collisions(out.get("user_context"))
    if collisions:
        out["ambiguity_job"]["detected_collisions"] = collisions
    instr = str(out.get("instructions") or "")
    if "AMBIGUITY PREFLIGHT" not in instr:
        out["instructions"] = (
            _AMBIGUITY_JOB_INSTRUCTIONS
            + (_COLLISION_CLAUSE if collisions else "")
            + instr
        )


def attach_ambiguity_job(payload: dict) -> dict:
    """Add ambiguity_job + step-0 instructions for host-side preflight. Never raises."""
    out = dict(payload or {})
    with never_raise(logger, "attach_ambiguity_job failed", exc_info=True) as g:
        _stamp_ambiguity_job(out)
    if g.failed:
        return dict(payload or {})
    return out
