"""Heuristic risk scorer for test cases.

Computes a risk score per TestCase from its Priority and TestType fields.
Never raises to callers — on any exception, returns the input list unchanged
with an empty section string.
"""

from __future__ import annotations

import logging

from tools.models import TestCase
from tools.risk_scorer_section import _build_risk_section

logger = logging.getLogger(__name__)

# Priority weights (higher = riskier)
_PRIORITY_WEIGHT: dict[str, int] = {
    "Critical": 4,
    "High": 3,
    "Medium": 2,
    "Low": 1,
}

# Type weights (higher = higher business/security impact)
_TYPE_WEIGHT: dict[str, int] = {
    "Security": 5,
    "Negative": 4,
    "Integration": 3,
    "Boundary": 2,
    "Exploratory": 2,
    "Functional": 1,
    "Regression": 1,
    "Smoke": 1,
    "Accessibility": 1,
    "Performance": 1,
}

# risk_score = priority_weight * 10 + type_weight
# Range: Low(1)*10 + 1 = 11  through  Critical(4)*10 + Security(5) = 45
# CRITICAL: score >= 34 (catches High+Security=35, High+Negative=34, Critical+*)
# HIGH:     score >= 24 (catches Medium+Security=25, Critical+Functional=41... all Critical are >=34)
# MEDIUM:   score >= 14 (catches Low+Security=15)
# LOW:      score <  14
_CRITICAL_THRESHOLD = 34
_HIGH_THRESHOLD = 24
_MEDIUM_THRESHOLD = 14


def _label_for(score: int) -> str:
    """The risk label for a score (threshold ladder)."""
    if score >= _CRITICAL_THRESHOLD:
        return "CRITICAL"
    if score >= _HIGH_THRESHOLD:
        return "HIGH"
    if score >= _MEDIUM_THRESHOLD:
        return "MEDIUM"
    return "LOW"


def _compute_risk(tc: TestCase) -> tuple[int, str, str]:
    """Return (risk_score, risk_label, risk_rationale) for a single TestCase."""
    p_weight = _PRIORITY_WEIGHT.get(tc.priority.value, 1)
    t_weight = _TYPE_WEIGHT.get(tc.type.value, 1)
    score = p_weight * 10 + t_weight
    label = _label_for(score)
    rationale = (
        f"Priority={tc.priority.value} (weight={p_weight}), "
        f"Type={tc.type.value} (weight={t_weight}) "
        f"→ {label} (score={score})"
    )
    return score, label, rationale


def build_risk_section(scored: list[TestCase]) -> str:
    """Public wrapper so callers (test_scenario_agent) can rebuild the Risk
    Summary from the FINAL renumbered suite after dedup + renumber (M1-risk),
    keeping the displayed table in lock-step with the exported file. Never raises."""
    try:
        return _build_risk_section(scored)
    except Exception:
        logger.exception("build_risk_section failed -- omitting risk table")
        return ""


def _score_cases(cases: list[TestCase]) -> list[TestCase]:
    """Copy every case with its risk fields set, sorted critical-first."""
    scored: list[TestCase] = []
    for tc in cases:
        score, label, rationale = _compute_risk(tc)
        update = {
            "risk_score": score,
            "risk_label": label,
            "risk_rationale": rationale,
        }
        scored.append(tc.model_copy(update=update))
    # Sort descending by risk_score (critical-first), stable on ties (preserves TC-ID order)
    scored.sort(key=lambda tc: tc.risk_score, reverse=True)
    return scored


def _log_scored(scored: list[TestCase]) -> None:
    logger.info(
        "Risk scoring complete: %d cases scored; top risk_label=%s",
        len(scored),
        scored[0].risk_label if scored else "N/A",
    )


# Returns (scored_sorted_cases, risk_section_markdown).
# On any error returns (cases, "") — never raises to callers.
def score_and_sort(cases: list[TestCase]) -> tuple[list[TestCase], str]:
    """Score cases by risk and sort critical-first."""
    if not cases:
        return cases, ""
    try:
        scored = _score_cases(cases)
        risk_section = _build_risk_section(scored)
        _log_scored(scored)
        return scored, risk_section
    except Exception:
        logger.exception(
            "Risk scoring failed — returning unsorted cases without risk section"
        )
        return cases, ""


# --------------------------------------------------------------------------- #
# Host-supplied risk verdicts -- DELETED 2026-08-16 (dead-code deletion P2-H)
#
# The chat-only overlay (the host `risk_scores` field), its 0-100 -> tier-label
# mapper and the three tier cut-offs are gone with the RISK_JOB cluster they
# served. The overlay was reached only when `host_risk_scores is not None`,
# which was provably never: `handlers` set `_risk_job = False` as a
# hardcoded literal, that local was the ONLY writer of `meta["host_risk_job"]`,
# and the submit keyed the extraction off that stamp. The SERVER-side half
# (`score_with_llm`, its response models, its prompt and its seam) went one
# batch earlier, in P2-F3.
#
# `score_and_sort` is now the only thing that scores a case, and the `note`
# parameter went with the overlay because the overlay was its only supplier.
# The ledger id "risk_scorer.llm_risk" STAYS in `host_llm.LEDGER_IDS` -- see
# docs/LLM_MIGRATION_INVENTORY.md.
#
# The absence of these symbols is pinned with `hasattr` in
# tests/test_risk_scoring_is_heuristic_only.py, never by searching this file
# for their names: this comment contains them.
# --------------------------------------------------------------------------- #
