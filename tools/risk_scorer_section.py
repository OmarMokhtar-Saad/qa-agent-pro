"""Markdown ``## Risk Summary`` section for scored test cases."""

from __future__ import annotations

from tools.models import TestCase

_LABELS = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


def _count_labels(scored: list[TestCase]) -> dict[str, int]:
    counts: dict[str, int] = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for tc in scored:
        counts[tc.risk_label] = counts.get(tc.risk_label, 0) + 1
    return counts


def _summary_lines(counts: dict[str, int]) -> list[str]:
    lines = ["\n\n## Risk Summary\n"]
    for label in _LABELS:
        n = counts.get(label, 0)
        if n:
            lines.append(f"- **{label}**: {n} test case{'s' if n != 1 else ''}")
    return lines


def _table_lines(scored: list[TestCase]) -> list[str]:
    lines = ["\n| TC ID | Title | Risk Label | Score | Rationale |"]
    lines.append("|-------|-------|-----------|-------|-----------|")
    for tc in scored:
        # Escape pipe chars in rationale so the table stays valid
        rationale_safe = tc.risk_rationale.replace("|", "\\|")
        lines.append(
            f"| {tc.tc_id} | {tc.title[:60]} | **{tc.risk_label}** "
            f"| {tc.risk_score} | {rationale_safe} |"
        )
    return lines


def _build_risk_section(scored: list[TestCase]) -> str:
    """Build the markdown ## Risk Summary section."""
    if not scored:
        return ""
    lines = _summary_lines(_count_labels(scored)) + _table_lines(scored)
    return "\n".join(lines)
