"""Heuristic risk scorer for test cases.

Computes a risk score per TestCase from its Priority and TestType fields.
Never raises to callers — on any exception, returns the input list unchanged
with an empty section string.
"""

from __future__ import annotations

import logging

from tools.models import TestCase

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


def _compute_risk(tc: TestCase) -> tuple[int, str, str]:
    """Return (risk_score, risk_label, risk_rationale) for a single TestCase."""
    p_weight = _PRIORITY_WEIGHT.get(tc.priority.value, 1)
    t_weight = _TYPE_WEIGHT.get(tc.type.value, 1)
    score = p_weight * 10 + t_weight

    if score >= _CRITICAL_THRESHOLD:
        label = "CRITICAL"
    elif score >= _HIGH_THRESHOLD:
        label = "HIGH"
    elif score >= _MEDIUM_THRESHOLD:
        label = "MEDIUM"
    else:
        label = "LOW"

    rationale = (
        f"Priority={tc.priority.value} (weight={p_weight}), "
        f"Type={tc.type.value} (weight={t_weight}) "
        f"→ {label} (score={score})"
    )
    return score, label, rationale


def _build_risk_section(scored: list[TestCase]) -> str:
    """Build the markdown ## Risk Summary section."""
    if not scored:
        return ""

    counts: dict[str, int] = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for tc in scored:
        counts[tc.risk_label] = counts.get(tc.risk_label, 0) + 1

    lines = ["\n\n## Risk Summary\n"]
    for label in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        n = counts.get(label, 0)
        if n:
            lines.append(f"- **{label}**: {n} test case{'s' if n != 1 else ''}")

    lines.append("\n| TC ID | Title | Risk Label | Score | Rationale |")
    lines.append("|-------|-------|-----------|-------|-----------|")
    for tc in scored:
        # Escape pipe chars in rationale so the table stays valid
        rationale_safe = tc.risk_rationale.replace("|", "\\|")
        lines.append(
            f"| {tc.tc_id} | {tc.title[:60]} | **{tc.risk_label}** "
            f"| {tc.risk_score} | {rationale_safe} |"
        )

    return "\n".join(lines)


def score_and_sort(cases: list[TestCase]) -> tuple[list[TestCase], str]:
    """Score cases by risk and sort critical-first.

    Returns (scored_sorted_cases, risk_section_markdown).
    On any error returns (cases, "") — never raises to callers.
    """
    if not cases:
        return cases, ""

    try:
        scored: list[TestCase] = []
        for tc in cases:
            score, label, rationale = _compute_risk(tc)
            scored.append(
                tc.model_copy(
                    update={
                        "risk_score": score,
                        "risk_label": label,
                        "risk_rationale": rationale,
                    }
                )
            )

        # Sort descending by risk_score (critical-first), stable on ties (preserves TC-ID order)
        scored.sort(key=lambda tc: tc.risk_score, reverse=True)

        risk_section = _build_risk_section(scored)
        logger.info(
            "Risk scoring complete: %d cases scored; top risk_label=%s",
            len(scored),
            scored[0].risk_label if scored else "N/A",
        )
        return scored, risk_section

    except Exception:
        logger.exception(
            "Risk scoring failed — returning unsorted cases without risk section"
        )
        return cases, ""
