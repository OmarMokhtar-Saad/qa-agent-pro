"""Markdown rendering of a FeatureAnalysisReport (leaf of feature_analysis)."""

from __future__ import annotations

from agents.feature_analysis_model import FeatureAnalysisReport

_EMPTY_NOTE = "_None identified._"


def _bullets(items: list[str]) -> str:
    """Render a list as markdown bullets, degrading to a graceful note when empty."""
    cleaned = [i.strip() for i in items if i and i.strip()]
    if not cleaned:
        return _EMPTY_NOTE
    return "\n".join(f"- {i}" for i in cleaned)


# COMPACT mode: the chat view keeps only the key sections (Feature Summary,
# Risks, Missing Requirements); the full analysis is delivered as a
# downloadable file. Emit those two sections and return before the verbose
# Requirement Analysis / UI Analysis / User Flow / Conflicts sections.
def render_report_markdown(report: FeatureAnalysisReport, compact: bool = False) -> str:
    """Render sections 1-6 of the report as markdown, in the user's exact order.

    Sections 7 (Test Case Table) and 8 (Coverage Report) come from the existing
    suite/summary and are deliberately NOT rendered here. Empty sections degrade
    to a graceful "_None identified._" note rather than being dropped. The
    "## Conflicts (Jira vs. Screenshots)" subsection is emitted under Requirement
    Analysis only when ``report.conflicts`` is non-empty.
    """
    lines = _summary_lines(report)
    if compact:
        lines.extend(_compact_lines(report))
        return "\n".join(lines)

    lines.extend(_requirement_lines(report))
    lines.extend(_closing_lines(report))
    return "\n".join(lines)


def _summary_lines(report: FeatureAnalysisReport) -> list[str]:
    """Section 1, Feature Summary (shared by the full and compact views)."""
    lines: list[str] = []
    lines.append("## Feature Summary")
    summary = (report.feature_summary or "").strip()
    lines.append(summary if summary else _EMPTY_NOTE)
    if report.business_objective.strip():
        lines.append("")
        lines.append(f"**Business objective:** {report.business_objective.strip()}")
    return lines


def _section_lines(sections: tuple, report: FeatureAnalysisReport) -> list[str]:
    lines: list[str] = []
    for heading, field in sections:
        lines.append(heading)
        lines.append(_bullets(getattr(report, field)))
    return lines


_COMPACT_SECTIONS = (
    ("## Risks", "risks"),
    ("## Missing Requirements", "missing_requirements"),
)

_REQUIREMENT_SECTIONS = (
    ("### Functional Requirements", "functional_requirements"),
    ("### Acceptance Criteria", "acceptance_criteria"),
    ("### Preconditions", "preconditions"),
    ("### User Roles", "user_roles"),
    ("### Dependencies", "dependencies"),
    ("### Validation Rules", "validation_rules"),
    ("### Error Handling", "error_handling"),
    ("### Edge Cases", "edge_cases"),
    ("### Assumptions", "assumptions"),
)

# 3. UI Analysis, 4. User Flow, 5. Missing Requirements, 6. Risks; each is
# preceded by a blank line.
_CLOSING_SECTIONS = (
    ("## UI Analysis", "ui_analysis"),
    ("## User Flow", "user_flow"),
    ("## Missing Requirements", "missing_requirements"),
    ("## Risks", "risks"),
)


def _compact_lines(report: FeatureAnalysisReport) -> list[str]:
    """The Risks and Missing Requirements sections of the compact view."""
    lines: list[str] = []
    for heading, field in _COMPACT_SECTIONS:
        lines.append("")
        lines.extend(_section_lines(((heading, field),), report))
    return lines


def _requirement_lines(report: FeatureAnalysisReport) -> list[str]:
    """Section 2, Requirement Analysis (with the optional Conflicts subsection)."""
    lines = ["", "## Requirement Analysis"]
    lines.extend(_section_lines(_REQUIREMENT_SECTIONS, report))
    if report.conflicts:
        lines.append("### Conflicts (Jira vs. Screenshots)")
        lines.append(_bullets(report.conflicts))
    return lines


def _closing_lines(report: FeatureAnalysisReport) -> list[str]:
    """Sections 3-6: UI Analysis, User Flow, Missing Requirements, Risks."""
    lines: list[str] = []
    for heading, field in _CLOSING_SECTIONS:
        lines.append("")
        lines.extend(_section_lines(((heading, field),), report))
    return lines
