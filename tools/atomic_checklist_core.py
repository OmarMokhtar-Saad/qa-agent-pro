"""Core of tools.atomic_checklist: shared constants, the item model and lexical helpers."""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

logger = logging.getLogger("tools.atomic_checklist")

# The five canonical EARS patterns (Mavin et al.), plus "complex" for a
# deliberate combination. An unknown tag from the model is folded to
# "ubiquitous" rather than rejected — never break generation over a label.
EARS_PATTERNS = (
    "ubiquitous",
    "event_driven",
    "state_driven",
    "optional",
    "unwanted",
    "complex",
)

_WORD_RE = re.compile(r"[\W_]+")

# Granularity audit thresholds (Phase 0). Deliberately module constants, not
# settings fields: they are a rubric, not an operator knob.
_SHORT_ITEM_WORDS = 4
_OVERLAP_SIM = 0.70
_MAX_OVERLAP_RATIO = 0.25
_MAX_SHORT_RATIO = 0.40
_MIN_PROVENANCE_RATIO = 0.50

_DEFAULT_CLUSTER_SIZE = 6  # research recommends 5-8 items per semantic cluster
_CLUSTER_JOIN_SIM = 0.15

# Prompt-block budget. MUST be large enough to hold QA_CHECKLIST_MAX_ITEMS
# rendered lines, or the tool truncates its own input and then reports the
# truncation as a coverage gap. A rendered line
# ("- CL-017 [event_driven] When the user taps cancel, the system shall
#   redirect the user to the Appointment Card screen.") is ~120 chars, so
# 200 items x 120 = 24,000, plus group headers and margin -> 32,000. Kept as a
# module constant so the getattr fallbacks below and config/settings.py cannot
# drift apart.
_DEFAULT_MAX_PROMPT_CHARS = 32000
_DEFAULT_MAX_ITEMS = 200
_MIN_PROMPT_CHARS = 200  # a cap below this would present nothing at all

# Cap on the ticket DESCRIPTION handed to Pass 1. Deliberately larger than the
# generation prompt's 3000-char jira_or_web_content slice: the description is
# where the alternate flows, message ids and state transitions live, and a
# checklist decomposed from a truncated body UNDER-COUNTS requirements, which is
# the defect this cap must not reintroduce. Public so
# agents/test_scenario_agent bounds its local with the SAME number instead of a
# duplicated literal that can drift.
MAX_DESCRIPTION_CHARS = 12000


class ChecklistItem(BaseModel):
    """One independently-verifiable outcome. ``item_id`` is assigned here, never
    by the model (mirrors rtm.AcceptanceCriterion / TestCase.stable_id)."""

    model_config = {"extra": "forbid"}

    item_id: str = Field(default="", description="CL-001 .. CL-NNN")
    text: str = Field(default="", description="The EARS-shaped outcome")
    ears_pattern: str = Field(default="ubiquitous", description="EARS pattern tag")
    source: str = Field(default="", description="Provenance tag (allowlisted)")


# The Pass-1 response models (_DecomposedItem / _Decomposition), the
# _DECOMPOSE_SYSTEM prompt and the ledger id `atomic_checklist.decompose`
# lived here until 2026-08-16 (dead-code deletion P2-F2). They existed only
# for decompose_to_checklist -- see its tombstone further down. The ledger id
# stays in tools/host_llm.LEDGER_IDS: that frozenset never shrinks.
#
# The EARS shape, the atomicity rule, the worked example and the source-tag
# allowlist that _DECOMPOSE_SYSTEM taught are NOT lost -- they are the
# instruction text of agents/host_checklist.CHECKLIST_JOB, which asks the tester's
# own model for the same decomposition. ``normalize_source`` and
# ``EARS_PATTERNS`` are LIVE and are what validate the host's reply.


def tokens(text: str) -> list[str]:
    return [t for t in _WORD_RE.split((text or "").lower()) if t]


def _norm(text: str) -> str:
    return " ".join(tokens(text))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    return (len(a & b) / len(union)) if union else 0.0


def _tokens(text: str) -> list[str]:
    """Legacy name of :func:`tokens` (WX removes it)."""
    return tokens(text)
