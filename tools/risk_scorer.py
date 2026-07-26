"""Heuristic risk scorer for test cases.

Computes a risk score per TestCase from its Priority and TestType fields.
Never raises to callers — on any exception, returns the input list unchanged
with an empty section string.
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import BaseModel, Field

from config.settings import settings
from llm import ask_json
from tools.models import TestCase
from tools.untrusted import _GUARD, wrap_untrusted

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


def _build_risk_section(scored: list[TestCase], note: str = "") -> str:
    """Build the markdown ## Risk Summary section.

    ``note`` is an optional italic line placed under the heading (used by the
    LLM-scoring path to flag that the scores are LLM-judged, not heuristic).
    """
    if not scored:
        return ""

    counts: dict[str, int] = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for tc in scored:
        counts[tc.risk_label] = counts.get(tc.risk_label, 0) + 1

    lines = ["\n\n## Risk Summary\n"]
    if note:
        lines.append(note + "\n")
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


def build_risk_section(scored: list[TestCase], note: str = "") -> str:
    """Public wrapper so callers (test_scenario_agent) can rebuild the Risk
    Summary from the FINAL renumbered suite after dedup + renumber (M1-risk),
    keeping the displayed table in lock-step with the exported file. Never raises."""
    try:
        return _build_risk_section(scored, note=note)
    except Exception:
        logger.exception("build_risk_section failed -- omitting risk table")
        return ""


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


# --------------------------------------------------------------------------- #
# LLM-based risk scoring (opt-in via settings.qa_llm_risk_scoring)
# --------------------------------------------------------------------------- #

# Bound the base timeout for the single batched call so a slow/hung backend
# can never stall the generation finish step. The timeout scales with suite size
# (see the asyncio.wait_for call) to give larger batches more headroom.
_LLM_RISK_TIMEOUT_S = 120

# The LLM risk_score is 0-100 (a richer scale than the heuristic's 11-45); these
# tier cut-offs turn it back into the same CRITICAL/HIGH/MEDIUM/LOW labels the
# rest of the pipeline (exports, RTM, summary) already renders.
_LLM_CRITICAL = 75
_LLM_HIGH = 50
_LLM_MEDIUM = 25

_LLM_RISK_SYSTEM = """\
You are a senior QA risk analyst. You are given a feature under test and a list
of already-written manual test cases (id, type, priority, title). Judge the
BUSINESS RISK of each test case's scenario failing in production, weighing:
- business impact (revenue, compliance, reputation),
- blast radius (how many users / how much of the system is affected),
- data-loss / data-corruption potential,
- exploitability (can an attacker abuse the failing behaviour).
For EACH test case return: its ROW number (the integer to the LEFT of the first
'|', EXACTLY as given), an integer risk_score from 0 (trivial) to 100
(catastrophic), and a single concise sentence of rationale. Score every test
case you are given. Output STRICTLY the JSON object for the schema, nothing else.
"""


class _RiskVerdict(BaseModel):
    # Keyed by an ENUMERATED ROW INDEX, never tc_id: pre-merge tc_ids collide
    # across categories (every category restarts at TC-001 and risk scoring runs
    # before the global renumber), so a tc_id map would collapse distinct cases.
    index: int = Field(
        default=-1, description="The 1-based row number of the test case, as given"
    )
    risk_score: int = Field(
        default=0, description="Business risk 0 (trivial) to 100 (catastrophic)"
    )
    rationale: str = Field(default="", description="One concise sentence")


class _RiskAssessment(BaseModel):
    verdicts: list[_RiskVerdict] = Field(default_factory=list)


def _llm_label(score: int) -> str:
    """Map a 0-100 LLM risk_score onto the shared risk-tier label."""
    if score >= _LLM_CRITICAL:
        return "CRITICAL"
    if score >= _LLM_HIGH:
        return "HIGH"
    if score >= _LLM_MEDIUM:
        return "MEDIUM"
    return "LOW"


async def score_with_llm(
    cases: list[TestCase], feature_text: str = ""
) -> tuple[list[TestCase], str]:
    """LLM-judged risk scoring with the heuristic as a never-fail fallback.

    Makes ONE batched ``ask_json`` call scoring ALL cases (payload capped to
    tc_id/type/priority/title plus the wrapped feature context). Returns the
    re-scored, critical-first list plus a markdown Risk Summary noting the
    scores are LLM-judged. On ANY failure (LLM error, timeout, no usable
    verdicts) it returns the heuristic ``score_and_sort`` result instead — never
    raises, never drops a case. Cases the LLM omits keep their heuristic score.
    """
    if not cases:
        return cases, ""

    # Heuristic baseline FIRST: guarantees every case is scored and provides the
    # fallback both for a total failure and for any tc_id the LLM leaves out.
    baseline, heuristic_section = score_and_sort(cases)
    try:
        # Enumerate an explicit ROW INDEX per case; the model echoes this index
        # back so verdicts map unambiguously even when pre-merge tc_ids collide.
        payload = "\n".join(
            f"{i} | {tc.type.value} | {tc.priority.value} | {tc.title[:120]}"
            for i, tc in enumerate(baseline, 1)
        )
        user = (
            "Feature under test:\n"
            + wrap_untrusted("feature_description", feature_text or "")
            + "\n\nTest cases to score (row | type | priority | title):\n"
            + payload
        )
        # Observability: large suites may hit LLM output-token limits.
        if len(cases) > 60:
            logger.info(
                "LLM risk scoring %d cases; may hit output-token limits and degrade to heuristic",
                len(cases),
            )
        assessment: _RiskAssessment = await asyncio.wait_for(
            ask_json(
                system=_LLM_RISK_SYSTEM + _GUARD,
                user=user,
                response_model=_RiskAssessment,
                model=settings.qa_classifier_model or None,
            ),
            timeout=min(300, _LLM_RISK_TIMEOUT_S + len(cases)),
        )
        verdicts = {
            v.index: v
            for v in assessment.verdicts
            if 1 <= v.index <= len(baseline) and 0 <= v.risk_score <= 100
        }
        if not verdicts:
            logger.info(
                "LLM risk scoring returned no usable verdicts — using heuristic"
            )
            return baseline, heuristic_section

        rescored: list[TestCase] = []
        llm_count = 0
        for i, tc in enumerate(baseline, 1):
            v = verdicts.get(i)
            if v is None:
                rescored.append(tc)  # keep heuristic score/label/rationale
                continue
            llm_count += 1
            label = _llm_label(v.risk_score)
            rescored.append(
                tc.model_copy(
                    update={
                        "risk_score": v.risk_score,
                        "risk_label": label,
                        "risk_rationale": (v.rationale.strip() or label)[:300],
                    }
                )
            )
        rescored.sort(key=lambda tc: tc.risk_score, reverse=True)

        total = len(rescored)
        if llm_count < total:
            note = (
                f"_Risk scores are **LLM-judged** (business impact, blast radius, "
                f"data-loss potential, exploitability) for {llm_count} of {total} "
                f"cases; the remaining {total - llm_count} keep the heuristic score._"
            )
        else:
            note = (
                "_Risk scores are **LLM-judged** (business impact, blast radius, "
                "data-loss potential, exploitability)._"
            )
        section = _build_risk_section(rescored, note=note)
        logger.info(
            "LLM risk scoring applied to %d/%d cases; top=%s",
            llm_count,
            total,
            rescored[0].risk_label if rescored else "N/A",
        )
        return rescored, section

    except Exception:
        logger.warning(
            "LLM risk scoring failed — falling back to heuristic scores",
            exc_info=True,
        )
        return baseline, heuristic_section
