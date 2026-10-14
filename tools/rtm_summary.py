"""The markdown RTM coverage table and coverage statistics."""

from __future__ import annotations

from tools.models import TestCase
from tools.rtm_sanitize import AcceptanceCriterion
from tools.rtm_tracemap import _trace_map


def _summary_table(
    acs: list[AcceptanceCriterion], ac_to_tcs: dict[str, list[str]]
) -> str:
    """The markdown RTM table of build_rtm_summary, one row per criterion."""
    rows: list[str] = []
    for ac in acs:
        linked = ac_to_tcs[ac.ac_id]
        linked_str = ", ".join(linked) if linked else ""
        status = "Covered" if linked else "ORPHAN"
        desc = (
            ac.description[:80] + "..." if len(ac.description) > 80 else ac.description
        )
        rows.append(f"| {ac.ac_id} | {desc} | {linked_str} | {status} |")

    return (
        "\n\n---\n\n"
        "## Requirements Traceability Matrix\n\n"
        "| AC ID | Acceptance Criterion | Linked TCs | Status |\n"
        "|-------|----------------------|------------|--------|\n" + "\n".join(rows)
    )


def _coverage_line(covered_count: int, total_count: int) -> str:
    """The bold coverage sentence, with the orphan-AC clause."""
    pct = int(covered_count / total_count * 100) if total_count else 0
    coverage_line = (
        f"\n\n**Coverage: {covered_count} of {total_count} ACs covered ({pct}%)."
    )
    if total_count - covered_count > 0:
        coverage_line += f" {total_count - covered_count} orphan AC(s) flagged.**"
    else:
        coverage_line += " All ACs covered.**"
    return coverage_line


def _orphan_tc_line(orphan_tc_ids: list) -> str:
    """The bold list of up to 20 cases that cite no requirement, else ""."""
    orphan_tc_line = ""
    if orphan_tc_ids:
        orphan_tc_line = (
            "\n\n**Orphan test cases (no linked requirement): "
            + ", ".join(orphan_tc_ids[:20])
            + (" ..." if len(orphan_tc_ids) > 20 else "")
            + "**"
        )
    return orphan_tc_line


def build_rtm_summary(
    acs: list[AcceptanceCriterion], test_cases: list[TestCase]
) -> str:
    """Build a markdown RTM coverage table and coverage stats.

    Returns empty string when acs is empty (no traceability data available).
    """
    if not acs:
        return ""

    ac_to_tcs, orphan_tc_ids = _trace_map(acs, test_cases)
    covered_count = sum(1 for tcs in ac_to_tcs.values() if tcs)
    table = _summary_table(acs, ac_to_tcs)
    coverage_line = _coverage_line(covered_count, len(acs))
    return table + coverage_line + _orphan_tc_line(orphan_tc_ids)
