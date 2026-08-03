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
from llm import ask_json, server_llm_scope
from tools.host_llm import open_task
from tools.untrusted import _GUARD, wrap_untrusted

logger = logging.getLogger(__name__)

# This module's row in docs/LLM_MIGRATION_INVENTORY.md. The STANDALONE
# qa_feature_analysis tool is CHAT-ONLY as of 2026-08-02 (qa_feature_analysis ->
# qa_submit_feature_analysis via tools/host_llm) and never reaches a backend.
# The LEGACY analyze_feature below survives for _finalize_generation's opt-in
# in-prep report and tags its call with this id.
_LEDGER_ID = "feature_analysis.report"

# Cap on each prompt input carried in the task record's meta, so a resubmit round
# can rebuild the prompt WITHOUT re-driving the mobile capture-another loop, and
# without the record growing unbounded (host_llm._cap re-applies its own cap).
_MAX_META_TEXT_CHARS = 20_000

# Inserted between the rubric and _GUARD on a resubmit round.
_RESUBMIT_REMINDER = (
    "\n\nYour previous submission carried no usable Feature Analysis object. "
    "Re-emit it as a SINGLE JSON object matching the response schema, with no "
    "prose outside the JSON."
)

# The two plain-string fields; every other field of FeatureAnalysisReport is a
# list[str]. Used by finalize_feature_report to coerce an UNTRUSTED submission.
_STR_FIELDS = ("feature_summary", "business_objective")


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


def build_feature_analysis_prompt(
    feature_text: str,
    jira_text: str,
    screenshot_descriptions: str,
    ui_content: dict | None,
    acs: list,
    *,
    reminder: str = "",
) -> tuple[str, str]:
    """Return ``(system_prompt, user_message)`` for ONE Feature Analysis pass.

    Extracted VERBATIM from the former ``analyze_feature`` body so the chat-only
    host path and the legacy coroutine below share ONE prompt and cannot drift --
    the same discipline as ``build_bug_report_prompt`` / ``build_coach_prompt``.
    ``reminder`` is the resubmit instruction inserted between the rubric and
    ``_GUARD`` on a retry round. Pure and never raises.

    PROVENANCE NOTE for the host path: the blocks below are each wrapped
    individually, but ``host_llm.build_envelope`` re-wraps the whole context and
    COLLAPSES them into ONE block, stripping the per-source labels (documented
    consequence #1 of the unconditional wrap). The plain markdown headings
    (``## Feature / Ticket Text``, ``## Parsed Acceptance Criteria``, ...) live in
    the BODY precisely so the host can still tell the sources apart.
    """
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
    return _SYSTEM_PROMPT + (reminder or "") + _GUARD, user_msg


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
        system, user_msg = build_feature_analysis_prompt(
            feature_text, jira_text, screenshot_descriptions, ui_content, acs
        )
        # LEGACY server-side call, KEPT deliberately. Its only remaining caller
        # is _finalize_generation's opt-in in-prep report
        # (agents/test_scenario_agent.py, gated by QA_HOST_FEATURE_REPORT_ENABLED
        # AND QA_FEATURE_ANALYSIS_ENABLED, both default OFF); the STANDALONE
        # qa_feature_analysis tool no longer reaches it -- it goes through
        # prepare_feature_analysis below. Tagged with this row's ledger id so
        # QA_SERVER_LLM_ENABLED governs it and
        # QA_SERVER_LLM_ALLOW=feature_analysis.report can revive exactly this one
        # path after the Phase-6 flip; untagged it would silently degrade to an
        # empty report instead.
        with server_llm_scope(_LEDGER_ID):
            return await ask_json(
                system=system,
                user=user_msg,
                response_model=FeatureAnalysisReport,
                model=settings.qa_classifier_model or None,
            )
    except Exception:
        logger.warning(
            "analyze_feature failed -- returning an empty report", exc_info=True
        )
        return FeatureAnalysisReport()


async def prepare_feature_analysis(
    feature_text: str,
    jira_text: str,
    screenshot_descriptions: str,
    *,
    ui_content: dict | None = None,
    acs: list | None = None,
    mode: str = "",
    screens: int = 0,
    source: str = "",
    round_no: int = 1,
    submit_tool: str = "qa_submit_feature_analysis",
) -> dict:
    """Open a host task asking the TESTER'S OWN chat model to write the report.

    No backend is contacted. The server still does everything only it can do --
    hardened Jira fetching, device capture, untrusted wrapping -- and, in the
    submit half, the coercion and the rendering. The prompt INPUTS ride on the
    task RECORD, never in the envelope, so a resubmit round can rebuild the
    prompt without re-driving the mobile capture loop and a host cannot alter
    them. Returns the house ``{"error", "content": {"task_id", "envelope"}}``
    dict; never raises.
    """
    try:
        system, user = build_feature_analysis_prompt(
            feature_text,
            jira_text,
            screenshot_descriptions,
            ui_content,
            list(acs or []),
            reminder=_RESUBMIT_REMINDER if int(round_no) > 1 else "",
        )
        return await open_task(
            "feature_analysis",
            system,
            user,
            return_field="report",
            response_schema=FeatureAnalysisReport.model_json_schema(),
            meta={
                "feature_text": (feature_text or "")[:_MAX_META_TEXT_CHARS],
                "jira_text": (jira_text or "")[:_MAX_META_TEXT_CHARS],
                "screen_descriptions": (screenshot_descriptions or "")[
                    :_MAX_META_TEXT_CHARS
                ],
                "mode": str(mode or ""),
                "screens": int(screens or 0),
                "source": str(source or ""),
                "round": int(round_no),
            },
            submit_tool=submit_tool,
        )
    except Exception as exc:
        logger.exception("prepare_feature_analysis failed")
        return {"error": str(exc), "content": None}


def finalize_feature_report(payload: object) -> tuple[FeatureAnalysisReport, bool]:
    """``(report, usable)`` from an UNTRUSTED host submission. Never raises.

    ``model_config = {"extra": "forbid"}`` would make a STRICT parse reject an
    otherwise perfect report just because the host volunteered one extra key, so
    unknown keys are DROPPED and known ones coerced field by field (a bare string
    where a list belongs becomes a one-item list; blanks are discarded).
    ``usable`` is False when nothing at all could be read -- the caller decides
    whether that earns a resubmit round. This never fabricates content to fill a
    section, which is the same rule ``_bullets`` follows when it renders
    ``_None identified._``.
    """
    if not isinstance(payload, dict):
        return FeatureAnalysisReport(), False
    data: dict = {}
    for name in FeatureAnalysisReport.model_fields:
        if name not in payload:
            continue
        value = payload[name]
        if name in _STR_FIELDS:
            if isinstance(value, str) and value.strip():
                data[name] = value.strip()
            continue
        if isinstance(value, str):
            value = [value]
        if isinstance(value, (list, tuple)):
            items = [str(i).strip() for i in value if str(i).strip()]
            if items:
                data[name] = items
    if not data:
        return FeatureAnalysisReport(), False
    try:
        return FeatureAnalysisReport(**data), True
    except Exception:
        logger.warning(
            "finalize_feature_report: could not coerce the host submission",
            exc_info=True,
        )
        return FeatureAnalysisReport(), False


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
