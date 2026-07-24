"""Enterprise Feature Analysis Report (opt-in).

A structured, LLM-generated report that merges Jira ticket content with mobile
screenshot descriptions into a single enterprise-QA "Feature Analysis Report",
produced ALONGSIDE the normal test-case suite by generate_test_scenarios when
``settings.qa_feature_analysis_enabled`` is on.

House rules honoured: this module must NOT import router.py; all LLM access goes
through ``llm.ask_json``; secrets come from ``.env`` via config.settings only; no
bare ``print()`` -- logging only. Never raises to its caller: ``analyze_feature``
returns an empty ``FeatureAnalysisReport`` on any failure, mirroring
``critique_coverage`` in test_scenario_agent.py.

Prompt-injection containment: every externally-sourced segment fed to the model
(Jira/feature text, parsed acceptance criteria, screenshot descriptions, and
extracted UI structure) is wrapped with ``tools.untrusted.wrap_untrusted`` and
the ``_GUARD`` note is appended to the system prompt at call time -- exactly the
contract the rest of test_scenario_agent.py already uses.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from config.settings import settings
from llm import ask_json
from tools.untrusted import _GUARD, wrap_untrusted

logger = logging.getLogger(__name__)


class FeatureAnalysisReport(BaseModel):
    """Structured enterprise feature-analysis report merged from Jira + screenshots."""

    model_config = {"extra": "forbid"}

    feature_summary: str = ""
    business_objective: str = ""
    functional_requirements: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    user_roles: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    validation_rules: list[str] = Field(default_factory=list)
    error_handling: list[str] = Field(default_factory=list)
    edge_cases: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    ui_analysis: list[str] = Field(default_factory=list)
    user_flow: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


_SYSTEM_PROMPT = """\
You are a senior enterprise QA business analyst. Produce a STRUCTURED Feature
Analysis Report by merging a Jira ticket with mobile app screenshots. Follow
these steps in order:

STEP 1 -- Extract requirements from the Jira/feature text: functional
requirements, acceptance criteria, preconditions, user roles, dependencies,
validation rules, and error handling.

STEP 2 -- Analyze the screenshots. Treat MULTIPLE screenshots as ONE CONNECTED
USER FLOW (screen 1 -> screen 2 -> ...), not as isolated images. Describe the UI
elements (ui_analysis) and reconstruct the end-to-end user_flow across all
screens in order.

STEP 3 -- MERGE both sources into one coherent picture. Requirements confirmed
by a screenshot are stronger; requirements present in only one source are still
included.

STEP 4 -- CONFLICT DETECTION: wherever the Jira ticket and the screenshots
DISAGREE (a field/label/rule/flow described one way in Jira and a different way
in the screenshots), REPORT the discrepancy in `conflicts`. Prefer the Jira
ticket as the source of truth when they conflict, BUT you must still report the
discrepancy -- never silently drop it.

STEP 5 -- Mark anything you inferred (rather than found stated) explicitly in
`assumptions`. NEVER invent business rules unless they are clearly inferable
from the provided material.

STEP 6 -- Populate `missing_requirements` with requirements a complete spec
would need but that are absent from BOTH sources.

STEP 7 -- RISK ANALYSIS: populate `risks` with the testing/quality risks implied
by the gaps, conflicts, and assumptions above.

Return ONLY the structured object. Every list may be empty; do not fabricate to
fill it.
"""


async def analyze_feature(
    feature_text: str,
    jira_text: str,
    screenshot_descriptions: str,
    ui_content: dict | None,
    acs: list,
) -> FeatureAnalysisReport:
    """Build a merged Feature Analysis Report via one structured LLM pass.

    Every untrusted segment is wrapped with wrap_untrusted and _GUARD is appended
    to the system prompt, so externally-sourced text can never impersonate an
    instruction. Never raises -- returns an empty ``FeatureAnalysisReport()`` on
    any failure so the caller (generate_test_scenarios) can simply omit the
    report, exactly like ``critique_coverage``'s never-raise contract.
    """
    try:
        # Bound token cost: cap the primary text before wrapping (reviewer note 1).
        primary = (jira_text or feature_text or "").strip() or "(none provided)"

        ac_block = ""
        if acs:
            ac_lines = "\n".join(
                f"- {getattr(ac, 'ac_id', '') or 'AC'}: {getattr(ac, 'description', '')}"
                for ac in acs
            )
            ac_block = "\n\n## Parsed Acceptance Criteria\n" + wrap_untrusted(
                "jira_acceptance_criteria", ac_lines
            )

        ui_block = ""
        if ui_content and not ui_content.get("error"):
            content = (ui_content.get("content") or "").strip()
            if content:
                ui_block = "\n\n## Live UI Structure\n" + wrap_untrusted(
                    "live_ui_structure", content[:3000]
                )

        user_msg = (
            "## Feature / Ticket Text\n"
            + wrap_untrusted("jira_or_web_content", primary)
            + ac_block
            + "\n\n## Screenshot Descriptions (treat as one connected flow)\n"
            + wrap_untrusted(
                "screenshot_descriptions",
                screenshot_descriptions or "(no screenshots provided)",
            )
            + ui_block
        )
        return await ask_json(
            system=_SYSTEM_PROMPT + _GUARD,
            user=user_msg,
            response_model=FeatureAnalysisReport,
            model=settings.qa_classifier_model or None,
        )
    except Exception:
        logger.warning(
            "analyze_feature failed -- returning an empty report", exc_info=True
        )
        return FeatureAnalysisReport()


_EMPTY_NOTE = "_None identified._"


def _bullets(items: list[str]) -> str:
    """Render a list as markdown bullets, degrading to a graceful note when empty."""
    cleaned = [i.strip() for i in items if i and i.strip()]
    if not cleaned:
        return _EMPTY_NOTE
    return "\n".join(f"- {i}" for i in cleaned)


def render_report_markdown(report: FeatureAnalysisReport, compact: bool = False) -> str:
    """Render sections 1-6 of the report as markdown, in the user's exact order.

    Sections 7 (Test Case Table) and 8 (Coverage Report) come from the existing
    suite/summary and are deliberately NOT rendered here. Empty sections degrade
    to a graceful "_None identified._" note rather than being dropped. The
    "## Conflicts (Jira vs. Screenshots)" subsection is emitted under Requirement
    Analysis only when ``report.conflicts`` is non-empty.
    """
    lines: list[str] = []

    # 1. Feature Summary
    lines.append("## Feature Summary")
    summary = (report.feature_summary or "").strip()
    lines.append(summary if summary else _EMPTY_NOTE)
    if report.business_objective.strip():
        lines.append("")
        lines.append(f"**Business objective:** {report.business_objective.strip()}")
    # COMPACT mode: the chat view keeps only the key sections (Feature Summary,
    # Risks, Missing Requirements); the full analysis is delivered as a
    # downloadable file. Emit those two sections and return before the verbose
    # Requirement Analysis / UI Analysis / User Flow / Conflicts sections.
    if compact:
        lines.append("")
        lines.append("## Risks")
        lines.append(_bullets(report.risks))
        lines.append("")
        lines.append("## Missing Requirements")
        lines.append(_bullets(report.missing_requirements))
        return "\n".join(lines)

    # 2. Requirement Analysis
    lines.append("")
    lines.append("## Requirement Analysis")
    lines.append("### Functional Requirements")
    lines.append(_bullets(report.functional_requirements))
    lines.append("### Acceptance Criteria")
    lines.append(_bullets(report.acceptance_criteria))
    lines.append("### Preconditions")
    lines.append(_bullets(report.preconditions))
    lines.append("### User Roles")
    lines.append(_bullets(report.user_roles))
    lines.append("### Dependencies")
    lines.append(_bullets(report.dependencies))
    lines.append("### Validation Rules")
    lines.append(_bullets(report.validation_rules))
    lines.append("### Error Handling")
    lines.append(_bullets(report.error_handling))
    lines.append("### Edge Cases")
    lines.append(_bullets(report.edge_cases))
    lines.append("### Assumptions")
    lines.append(_bullets(report.assumptions))
    if report.conflicts:
        lines.append("### Conflicts (Jira vs. Screenshots)")
        lines.append(_bullets(report.conflicts))

    # 3. UI Analysis
    lines.append("")
    lines.append("## UI Analysis")
    lines.append(_bullets(report.ui_analysis))

    # 4. User Flow
    lines.append("")
    lines.append("## User Flow")
    lines.append(_bullets(report.user_flow))

    # 5. Missing Requirements
    lines.append("")
    lines.append("## Missing Requirements")
    lines.append(_bullets(report.missing_requirements))

    # 6. Risks
    lines.append("")
    lines.append("## Risks")
    lines.append(_bullets(report.risks))

    return "\n".join(lines)
