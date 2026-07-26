"""Requirement ambiguity pre-pass (T-11 / I-048 / A-032).

Garbage-in dominates test-generation quality: a vague one-liner ("make it fast
and nice") yields 96 hollow cases. Before spending the 8-category fan-out, this
module runs one cheap LLM pass to spot ambiguity/under-specification and surface
a few clarifying questions, so app.py can offer the tester a chance to clarify
first (or generate anyway).

Never raises — on any failure returns a benign "no issues" result so generation
is never blocked by the pre-pass itself.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field

from config.settings import settings
from llm import ask_json
from tools.untrusted import _GUARD, wrap_untrusted

logger = logging.getLogger(__name__)

Severity = Literal["none", "low", "medium", "high"]


class RequirementIssues(BaseModel):
    severity: Severity = Field(
        description="Overall ambiguity/under-specification severity of the feature text"
    )
    issues: list[str] = Field(
        default_factory=list,
        description="Short descriptions of what is unclear or missing",
    )
    questions: list[str] = Field(
        default_factory=list,
        description="Up to 3 concrete clarifying questions to ask the tester",
    )
    testable_surface: Literal["ui", "api", "backend", "docs", "none", "unclear"] = (
        Field(
            default="unclear",
            description="Where the feature can be exercised: 'ui' (user-facing "
            "screen), 'api', 'backend', 'docs' (documentation/config only), "
            "'none' (nothing manually testable), or 'unclear'.",
        )
    )


_SYSTEM = """\
You are a senior QA analyst reviewing a feature description BEFORE test cases are
generated. Judge how clear and testable it is.

Return:
- severity: "none" (clear enough to test), "low", "medium", or "high" (too vague
  to produce good test cases).
- issues: short bullet phrases naming what is ambiguous, missing, or contradictory
  (e.g. "no acceptance criteria", "'fast' is not quantified", "no error handling
  described"). Empty when severity is "none".
- questions: at most 3 specific, answerable clarifying questions that would most
  improve the tests. Empty when severity is "none".

Be pragmatic: a normal, reasonably-detailed feature description is "none" or
"low". Reserve "high" for genuinely unusable input (a few words, pure vibes, or
self-contradiction).

No-UI / backend detection (SHYJ-7154): ALSO set severity to "high" — and make
ONE of the questions ask WHERE the feature can be exercised (the application
URL, the environment, or whether API/artifact-level testing is expected) — when
the description is a backend, API, infrastructure, documentation, or
configuration change with NO user-facing screen a manual tester could open, OR
when it references an issue/ticket link but names no application URL or UI to
navigate. Set testable_surface to the best fit: "ui", "api", "backend", "docs",
"none", or "unclear".
"""

_SAFE_DEFAULT = {
    "severity": "none",
    "issues": [],
    "questions": [],
    "testable_surface": "unclear",
}


async def analyze_requirements(text: str) -> dict:
    """Analyse a feature description for ambiguity. Never raises.

    Returns ``{"severity": str, "issues": [...], "questions": [...]}``. On empty
    input or any failure, returns the benign default (severity "none") so the
    caller proceeds with generation.
    """
    try:
        if not text or not text.strip():
            return dict(_SAFE_DEFAULT)
        result: RequirementIssues = await ask_json(
            system=_SYSTEM + _GUARD,
            user=wrap_untrusted("feature_description", text),
            response_model=RequirementIssues,
            model=settings.qa_classifier_model or None,
        )
        return {
            "severity": result.severity,
            "issues": list(result.issues),
            "questions": list(result.questions[:3]),
            "testable_surface": result.testable_surface,
        }
    except Exception:
        logger.warning(
            "analyze_requirements failed — proceeding as if no issues", exc_info=True
        )
        return dict(_SAFE_DEFAULT)


SEVERITY_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}


def gate_triggers(result: dict, gate: str) -> bool:
    """True when an analyze_requirements result meets/exceeds the *gate* severity
    and carries at least one clarifying question. gate=="off" never triggers.

    Used by the non-interactive MCP path to honour
    QA_AMBIGUITY_GATE_SEVERITY. Never raises — any failure returns
    False so generation is never blocked by the gate helper itself.
    """
    try:
        gate = (gate or "high").strip().lower()
        if gate == "off":
            return False
        severity = str(result.get("severity", "none")).lower()
        questions = result.get("questions") or []
        return SEVERITY_ORDER.get(severity, 0) >= SEVERITY_ORDER.get(gate, 3) and bool(
            questions
        )
    except Exception:
        logger.warning("gate_triggers failed — not gating", exc_info=True)
        return False
