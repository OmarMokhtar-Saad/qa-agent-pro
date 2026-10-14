"""Trace map: which test cases cite which acceptance criterion, and the orphans."""

from __future__ import annotations

import logging

from tools.models import TestCase, normalize_ac_id
from tools.rtm_labels import _leading_label, _norm_label
from tools.rtm_sanitize import AcceptanceCriterion

logger = logging.getLogger("tools.rtm")


def _canonical_index(acs: list[AcceptanceCriterion]) -> dict[str, str]:
    """Index each criterion by its normalized id, then by its leading label."""
    norm_to_canonical: dict[str, str] = {
        normalize_ac_id(ac.ac_id): ac.ac_id for ac in acs
    }
    # 2026-08-31 (F4): a criterion parsed out of `BR07: ...` keeps its own label
    # in the text, and a generator asked to cite "the acceptance criterion this
    # verifies" cites THAT label, not the synthetic AC-00n id it has never been
    # shown. Both indexes are consulted, so `requirement_id: "BR07"` traces --
    # instead of the workbook printing BR07 in the Requirement ID column and
    # declaring, two sheets later, that no case carries a usable one.
    for _ac in acs:
        _label = _leading_label(getattr(_ac, "description", ""))
        if _label:
            norm_to_canonical.setdefault(_label, _ac.ac_id)
    return norm_to_canonical


# Extracted so build_rtm_summary, rtm_trace and traceability_warning_section all
# read ONE computation instead of three traversals that could disagree. Match on
# the *normalized* id so a case tagged "AC-1"/"ac001" still traces to canonical
# "AC-001".
def _trace_map(
    acs: list[AcceptanceCriterion], test_cases: list[TestCase]
) -> tuple[dict, list]:
    """Map each AC id -> the tc_ids citing it, plus the cases citing nothing."""
    ac_to_tcs: dict[str, list[str]] = {ac.ac_id: [] for ac in acs}
    norm_to_canonical = _canonical_index(acs)
    orphan_tc_ids: list[str] = []
    for tc in test_cases:
        canonical = norm_to_canonical.get(
            normalize_ac_id(tc.requirement_id)
        ) or norm_to_canonical.get(_norm_label(tc.requirement_id))
        if canonical:
            ac_to_tcs[canonical].append(tc.tc_id)
        else:
            orphan_tc_ids.append(tc.tc_id)
    return ac_to_tcs, orphan_tc_ids


def rtm_trace(acs: list, test_cases: list) -> dict:
    """The traceability outcome as DATA, for the audit trail.

    build_rtm_summary has always PRINTED these numbers; nothing carried them out,
    so "is traceability degenerate?" needed a hand investigation. Never raises --
    an unreadable suite yields zeros rather than breaking a generation.
    """
    try:
        if not acs:
            return {"acs": 0, "covered": 0, "traced_cases": 0, "orphan_cases": 0}
        ac_to_tcs, orphan_tc_ids = _trace_map(acs, test_cases)
        return {
            "acs": len(acs),
            "covered": sum(1 for tcs in ac_to_tcs.values() if tcs),
            "traced_cases": sum(len(tcs) for tcs in ac_to_tcs.values()),
            "orphan_cases": len(orphan_tc_ids),
        }
    except Exception:
        logger.exception("rtm_trace failed -- returning zeros")
        return {"acs": 0, "covered": 0, "traced_cases": 0, "orphan_cases": 0}


def _orphan_limit(cap: int) -> int:
    """The non-negative number of orphans to name; 20 when ``cap`` is not an int."""
    try:
        limit = max(0, int(cap))
    except (TypeError, ValueError, OverflowError):
        limit = 20
    return limit


# rtm_trace already COUNTS them; the submit-side nudge needs to NAME a few, and
# rtm_trace's dict is asserted byte-for-byte by tests/test_rtm.py, so this is a
# second reader of the SAME _trace_map computation rather than a new key on a
# contract other code already depends on. Order is the suite's own; the list is
# capped because it is rendered into a tester-facing note.
#
# Never raises -- an unreadable suite yields [] rather than breaking a
# generation, exactly like rtm_trace beside it.
def orphan_case_ids(acs: list, test_cases: list, *, cap: int = 20) -> list:
    """The tc_ids of the cases that trace to NO acceptance criterion."""
    try:
        if not acs or not test_cases:
            return []
        _ac_to_tcs, orphan_tc_ids = _trace_map(acs, test_cases)
        limit = _orphan_limit(cap)
        return [str(t) for t in orphan_tc_ids][:limit]
    except Exception:
        logger.exception("orphan_case_ids failed -- returning []")
        return []
