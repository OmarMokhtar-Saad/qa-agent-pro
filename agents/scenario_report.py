"""Report fragments of a finalized scenario suite: checklist, rule packs, counts."""

from __future__ import annotations

import logging

from agents.scenario_categories import CATEGORIES
from agents.scenario_status import _BEST_EFFORT_ERRORS
from tools.atomic_checklist import (
    checklist_to_dicts,
    granularity_warning_section,
)
from tools.rule_packs import rule_pack_notes, rule_pack_section

logger = logging.getLogger("agents.test_scenario_agent")


def _checklist_section(suite, checklist_items, checklist_audit) -> str:
    """The checklist GRANULARITY audit section ("" when no checklist was
    built), attaching the checklist artifacts to ``suite`` for the exporters.
    Never raises on the attach."""
    if not checklist_items:
        return ""
    section = granularity_warning_section(checklist_audit)
    try:
        suite._checklist_artifacts = {
            "items": checklist_to_dicts(checklist_items),
            "audit": checklist_audit,
        }
    except _BEST_EFFORT_ERRORS:
        logger.debug("attaching checklist artifacts failed", exc_info=True)
    return section


def _rule_pack_report(suite, renumbered, packs_and_ctx) -> str:
    """The rule-pack advisory report + the MECHANICAL [ASSUMED] notes, on the
    FINAL renumbered suite so every tc_id matches the exported file. The
    assumption label is a fixed code constant plus the sanitised ticket
    reference, never an LLM-written citation. ``packs_and_ctx`` is
    ``(rule_packs, rule_pack_ctx)``."""
    rule_packs, rule_pack_ctx = packs_and_ctx
    rule_pack_notes_map = rule_pack_notes(renumbered, rule_packs)
    if rule_pack_notes_map:
        try:
            suite._rule_pack_notes = rule_pack_notes_map
        except _BEST_EFFORT_ERRORS:
            logger.debug("attaching rule-pack notes failed", exc_info=True)
    rule_pack_ctx["notes"] = rule_pack_notes_map
    return rule_pack_section(rule_packs, renumbered, rule_pack_ctx)


def _priority_and_risk_counts(test_cases) -> tuple[str, dict[str, int]]:
    """The "N Critical, N High, ..." priority summary and the per-label risk
    counts, shared by the compact and verbose summaries."""
    priority_counts: dict[str, int] = {}
    risk_counts: dict[str, int] = {}
    for tc in test_cases:
        priority_counts[tc.priority.value] = (
            priority_counts.get(tc.priority.value, 0) + 1
        )
        if tc.risk_label:
            risk_counts[tc.risk_label] = risk_counts.get(tc.risk_label, 0) + 1

    order = ["Critical", "High", "Medium", "Low"]
    priority_summary = ", ".join(
        f"{priority_counts[p]} {p}" for p in order if p in priority_counts
    )
    return priority_summary, risk_counts


def _partial_warning(failed) -> tuple[str, str]:
    """The skipped-categories warning and the run status ("partial"/"ok")."""
    if not failed:
        return "", "ok"
    skipped_names = ", ".join(f"**{r.category_name}**" for r in failed)
    partial_warning = (
        f"\n\n> ⚠️  {len(failed)} of {len(CATEGORIES)} test categories couldn't be completed "
        f"({skipped_names}) — those test cases aren't included, "
        "but everything else is here."
    )
    return partial_warning, "partial"


def _risk_line(risk_counts: dict[str, int]) -> str:
    """The compact summary's one-line risk breakdown ("" when no case has a
    risk label)."""
    risk_summary = " · ".join(
        f"{label.upper()} {risk_counts[label]}"
        for label in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
        if label in risk_counts
    )
    return f"\n\n**Risk:** {risk_summary}" if risk_summary else ""


_XLSX_WARNING = (
    "\n\n> ⚠️  The Excel file couldn't be created this time "
    "(there may be a disk space or file permission issue). "
    "The test case list above is complete — you can paste it into your test tool manually."
)
_CSV_WARNING = (
    "\n\n> ⚠️  The CSV export couldn't be created this time "
    "(there may be a disk space or file permission issue). "
    "The test case list above is complete."
)
_TESTRAIL_WARNING = (
    "\n\n> ⚠️  The TestRail CSV export couldn't be created this time "
    "(there may be a disk space or file permission issue). "
    "The other files above are unaffected."
)
_XLSX_FILE_NOTE = (
    "\n\nThe Excel file is attached below. "
    "All test cases default to **Not Run** status. "
    "Use the **Status** dropdown in column N to record results as you execute."
)
