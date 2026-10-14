"""Summary composition of a finalized scenario suite: section builders and order."""

from __future__ import annotations

from typing import NamedTuple

from agents import scenario_grounding, scenario_suppression
from agents.scenario_args import GroundingSource
from agents.scenario_categories import CategoryResult
from agents.scenario_prepared import PreparedGeneration
from agents.scenario_report import (
    _checklist_section,
    _partial_warning,
    _priority_and_risk_counts,
    _rule_pack_report,
)
from tools.ac_anchor import anchoring_warning_section, scope_warning_section
from tools.models import TestCase, TestSuite
from tools.quality_checks import data_notes_section, quality_warning_section
from tools.rtm import build_rtm_summary, rtm_oneline, traceability_warning_section

# ops-4c: the DETERMINISTIC quality warnings print ahead of the
# variable-length sections. checklist_section grows one line per
# requirement, so with it in front shape_generation_result's 4000-char cap
# could silently delete the Data Quality Notes -- the ONLY report that a step
# is too vague to execute. Advisory prose gets truncated instead. Both orders
# keep quality ahead of checklist.
_COMPACT_SUMMARY_ORDER = (
    "risk_line",
    "rtm_line",
    "quality",
    "consistency",
    "grounding",
    "host_suppress",
    "checklist",
    "test_data",
    "anchoring",
    "scope",
    "rule_pack",
    "semantic_dedup",
)
_FULL_SUMMARY_ORDER = (
    "file_note",
    "rtm",
    "quality",
    "consistency",
    "grounding",
    "host_suppress",
    "checklist",
    "risk",
    "test_data",
    "anchoring",
    "scope",
    "rule_pack",
    "semantic_dedup",
    "export",
)


class _Scoring(NamedTuple):
    """What scoring produced: the final cases, the rule-pack context and the
    stable ids flagged as out of scope."""

    renumbered: list[TestCase]
    rule_pack_ctx: dict
    out_of_scope_ids: set[str]


def _compose_summary(head: str, sections: dict, order: tuple[str, ...]) -> str:
    """``head`` followed by each named section, in ``order``."""
    return head + "".join(f"{sections[name]}" for name in order)


def _quality_sections(renumbered, checklist_section: str) -> dict[str, str]:
    """The deterministic quality block and the host-suppression disclosure
    that sits next to it, keyed by their summary section names."""
    # Cheap heuristic quality gate: flags vague steps / placeholder test data
    # that survived generation. Never raises.
    quality_section = quality_warning_section(renumbered)
    # Residue R2: the two server-side review steps a HOST submit suppressed,
    # disclosed next to the quality block, AHEAD of the variable-length
    # sections (the ops-4c reply-cap reason). "" on every server route.
    host_suppress_section = scenario_suppression._host_suppression_section(
        renumbered,
        deterministic_coverage=bool(checklist_section),
    )
    return {"quality": quality_section, "host_suppress": host_suppress_section}


def _grounding_source(prepared, source_acs) -> GroundingSource:
    return GroundingSource(
        getattr(prepared, "target_description", "") or "",
        user_msg=prepared.user_msg,
        ac_texts=[ac.description for ac in (source_acs or [])],
    )


def _advisory_sections(prepared, renumbered, out_of_scope_ids) -> dict[str, str]:
    """Advisory sections by name; TICKET-7154 Fix 3 anchors only source-parsed
    ACs, scope is flag-only on FINAL tc_ids, "" when clean."""
    source_acs = prepared.source_acs
    test_data_section = data_notes_section(renumbered)
    anchoring_section = anchoring_warning_section(renumbered, source_acs)
    scope_section = scope_warning_section(renumbered, out_of_scope_ids)
    consistency_section, grounding_section = scenario_grounding.grounding_sections(
        renumbered,
        _grounding_source(prepared, source_acs),
    )
    return {
        "test_data": test_data_section,
        "anchoring": anchoring_section,
        "scope": scope_section,
        "consistency": consistency_section,
        "grounding": grounding_section,
    }


def _rtm_headline(acs: list, source_acs: list, suite: TestSuite) -> str:
    # 2026-08-03: `acs` may be MODEL-DERIVED (the host's AC_JOB when the ticket
    # carried none, with source_acs left empty), so the two fields together ARE
    # the provenance; the headline must not call invented criteria "traced".
    return rtm_oneline(acs, suite.test_cases, derived=bool(acs) and not source_acs)


# _report_sections: the traceability counts are carried OUT as data (see
# _attach_rtm_data); coverage is deterministic requirement_id traceability only
# (no similarity scoring, no percentage), FLAG ONLY like the advisory sections.
def _report_sections(
    prepared: PreparedGeneration, suite: TestSuite, scoring: _Scoring
) -> dict[str, str]:
    """The summary sections built from the FINAL renumbered suite."""
    renumbered = scoring.renumbered
    rtm_section = build_rtm_summary(prepared.acs, renumbered)
    rtm_section += traceability_warning_section(prepared.acs, renumbered)
    checklist_section = _checklist_section(
        suite, prepared.checklist_items, prepared.checklist_audit
    )
    packs_and_ctx = (prepared.rule_packs, scoring.rule_pack_ctx)
    rule_pack_section_md = _rule_pack_report(suite, renumbered, packs_and_ctx)
    return {
        "rtm": rtm_section,
        "checklist": checklist_section,
        "rule_pack": rule_pack_section_md,
        **_quality_sections(renumbered, checklist_section),
        **_advisory_sections(prepared, renumbered, scoring.out_of_scope_ids),
    }


def _summary_head(
    suite: TestSuite,
    category_results: list[CategoryResult],
    prepared: PreparedGeneration,
) -> tuple[str, str, dict[str, int]]:
    """(summary head, run status, per-label risk counts) shared by both summaries."""
    priority_summary, risk_counts = _priority_and_risk_counts(suite.test_cases)
    partial_warning, status = _partial_warning(
        [r for r in category_results if not r.succeeded]
    )
    head = (
        f"Generated **{len(suite.test_cases)} test cases** ({priority_summary})."
        f"{partial_warning}"
        f"{prepared.image_notice}"
    )
    return head, status, risk_counts
