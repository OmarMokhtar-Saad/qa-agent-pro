"""Phases of finalizing a generated suite: dedupe, score, renumber, trace."""

from __future__ import annotations

import logging

from agents.scenario_dedupe import _dedupe_cases
from agents.scenario_prepared import PreparedGeneration
from agents.scenario_status import _BEST_EFFORT_ERRORS
from tools import settings_port
from tools.ac_anchor import filter_unanchored_cases, flag_out_of_scope_cases
from tools.models import TestCase, TestSuite
from tools.quality_checks import (
    normalize_module_names,
    resolve_chained_refs_to_stable,
    restore_chained_refs_from_stable,
)
from tools.risk_scorer import score_and_sort
from tools.rtm import AcceptanceCriterion, orphan_case_ids, rtm_rows, rtm_trace
from tools.rule_packs import apply_rule_packs, inject_manual_validation_case

logger = logging.getLogger("agents.test_scenario_agent")

_PRIORITY_ORDER = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
_TYPE_ORDER = {
    "Functional": 0,
    "Smoke": 1,
    "Regression": 2,
    "Integration": 3,
    "Negative": 4,
    "Boundary": 5,
    "Security": 6,
    "Performance": 7,
    "Accessibility": 8,
    "Exploratory": 9,
}


def _funnel_counts(suite, renumbered) -> tuple[int, int, int, int]:
    """(cases in suite, empty test_data, total shipped, distinct module labels)."""
    modules = {(getattr(tc, "module", "") or "").strip() for tc in renumbered}
    empty = sum(1 for tc in renumbered if not getattr(tc, "test_data", None))
    cases = len(getattr(suite, "test_cases", None) or [])
    return cases, empty, len(renumbered), len(modules - {""})


def _log_finalize_funnel(suite, renumbered, quality_section: str) -> None:
    """The one closing funnel log line. Never raises."""
    # ops-5 (issue 7) + TICKET-5138: ONE line with the count, whether the quality
    # gate flagged anything, the empty Test Data count and the Module label
    # count (a single-label or fragmented column is otherwise only visible in
    # the workbook), all over `renumbered`, the cases actually shipped.
    try:
        cases, empty, total, labels = _funnel_counts(suite, renumbered)
        logger.info(
            "finalize: %d case(s) final | quality flags=%s"
            " | empty test_data=%d/%d | module labels=%d",
            cases,
            "yes" if quality_section else "no",
            empty,
            total,
            labels,
        )
    except _BEST_EFFORT_ERRORS:
        logger.debug("finalize summary log failed", exc_info=True)


def _presort_by_priority_and_type(all_cases: list[TestCase]) -> None:
    """Stable in-place presort; a tie-breaker only, score_and_sort decides."""
    all_cases.sort(
        key=lambda tc: (
            _PRIORITY_ORDER.get(tc.priority.value, 99),
            _TYPE_ORDER.get(tc.type.value, 99),
        )
    )


def _renumber_and_restore_refs(scored: list[TestCase]) -> list[TestCase]:
    renumbered = [
        tc.model_copy(update={"tc_id": f"TC-{i:03d}"}) for i, tc in enumerate(scored, 1)
    ]
    # Restore each case's chained_from (held as the target's content stable_id)
    # to the target's FINAL tc_id; a stable_id whose case was deduped/dropped is
    # cleared (dangling).
    return restore_chained_refs_from_stable(renumbered)


def _dedupe_and_log(all_cases: list[TestCase]) -> list[TestCase]:
    received = len(all_cases)
    deduped = _dedupe_cases(all_cases)
    # ops-5 (issue 7): the case-count funnel, so a silent regression shows in
    # the log instead of requiring a code read.
    logger.info(
        "finalize: received %d case(s) -> %d after exact dedup",
        received,
        len(deduped),
    )
    return deduped


def _attach_rtm_data(
    suite: TestSuite, prepared: PreparedGeneration, renumbered: list[TestCase]
) -> None:
    """Carry the traceability data OUT on private attrs of the suite."""
    acs, source_acs = prepared.acs, prepared.source_acs
    try:
        setattr(suite, "_rtm_trace", rtm_trace(acs, renumbered))
        # The submit-side nudge NAMES a few orphans: the FINAL ids, capped in
        # tools/rtm.orphan_case_ids.
        setattr(suite, "_rtm_orphan_ids", orphan_case_ids(acs, renumbered))
        # F06: the workbook's traceability sheet; `derived` (acs set, source_acs
        # empty) means the host SYNTHESIZED them, and the sheet says so.
        suite._rtm_artifacts = {
            "rows": rtm_rows(acs, renumbered, derived=bool(acs) and not source_acs)
        }
    except _BEST_EFFORT_ERRORS:  # pragma: no cover - rtm_trace never raises
        logger.debug("could not attach _rtm_trace", exc_info=True)


# _dedupe_for_finalize: resolve chained refs (tc_id -> stable_id) BEFORE the
# exact dedup, which drops CONTENT-identical cases that share a stable_id with
# the surviving twin; resolving after would turn the same ref dangling. Never
# raises. TICKET-7154 Fix 3: with REAL source ACs, optionally drop cases citing a
# non-existent AC id (flag QA_AC_ANCHORING_ENFORCE, default OFF; never empties
# the suite); the advisory warning always runs.
def _dedupe_for_finalize(
    all_cases: list[TestCase], source_acs: list[AcceptanceCriterion]
) -> list[TestCase]:
    """Resolve chained refs, exact-dedupe, then optionally drop unanchored cases."""
    all_cases = resolve_chained_refs_to_stable(all_cases)
    all_cases = _dedupe_and_log(all_cases)
    if source_acs and settings_port.get().qa_ac_anchoring_enforce:
        all_cases = filter_unanchored_cases(all_cases, source_acs)
    return all_cases


# _score_and_scope: score_sort critical-first (never raises; the deterministic
# heuristic is the only scorer, the presort a pure tie-breaker), then the rule
# packs (deterministic placeholder substitution, the native-speaker bilingual
# case appended last so nothing rewrites it), then the sub-task scope check
# (advisory, FLAG-ONLY): after the LAST content mutation and before the
# renumber, so each exported case is checked once against its final content.
def _score_and_scope(
    all_cases: list[TestCase], prepared: PreparedGeneration
) -> tuple[list[TestCase], str, dict, set[str]]:
    """(scored cases, risk section, rule-pack context, out-of-scope stable ids)."""
    _presort_by_priority_and_type(all_cases)
    scored, risk_section = score_and_sort(all_cases)
    scored, rule_pack_ctx = apply_rule_packs(scored, prepared.rule_packs)
    scored = inject_manual_validation_case(scored, prepared.rule_packs)
    out_of_scope_ids: set[str] = set()
    if prepared.parent_context:
        out_of_scope_ids = flag_out_of_scope_cases(
            scored, prepared.feature_text, prepared.parent_context
        )
    return scored, risk_section, rule_pack_ctx, out_of_scope_ids


# _renumber_and_normalize: TC-001..N in the FINAL row order (model_copy, never
# direct mutation, so validators hold), then module-name canonicalization
# (casing pass + qualifier-prefix merge on TAIL containment only; see
# tools/quality_checks._qualifier_prefix_merges). Deterministic, never drops or
# reorders a case.
def _renumber_and_normalize(scored: list[TestCase]) -> list[TestCase]:
    """Final TC-NNN ids, restored chained refs, canonical module names."""
    renumbered = _renumber_and_restore_refs(scored)
    return normalize_module_names(renumbered, merge_qualifier_prefixes=True)
